"""Отложенная проверка концов (RFC 39 §3.5, `relation_verify`).

Как `question_verify`: проверяет `code:`/`url:` и ссылки вперёд, штампует
`resolved` / `broken_reason` / `last_checked` и дописывает `to_uid`, когда
сущность появилась. Это производное состояние: ни ревизий, ни событий — иначе
ночная проверка завалила бы историю отметками.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.relations import ENTITY_KINDS, EntityRef, RefKind, RefSyntaxError, parse_ref
from cod_doc.infra.models import RelationModel
from cod_doc.services import link_service
from cod_doc.services.entity_ref import resolve_many

from ._types import RelationError, VerifyReport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

_URL = re.compile(r"^https?://\S+$")
_REASON_MAX = 32  # relation.broken_reason


def _check_literal(session: Session, project_id: int, row: RelationModel) -> str | None:
    """`broken_reason` или `None`; ссылка вперёд и литерал проверяются раздельно."""
    key = row.to_key or ""
    if row.to_kind == RefKind.URL.value:
        return None if _URL.fullmatch(key) else "bad_url"
    path, _, symbol = key.partition("::")
    ok, _, reason = link_service.resolve_code_ref(session, project_id, path, symbol or None)
    return None if ok else (reason or "code_ref_broken")[:_REASON_MAX]


def verify(session: Session, *, project_id: int, refs: Sequence[str] | None = None) -> VerifyReport:
    """Проверить рёбра с `to_uid IS NULL`; `refs` — только рёбра этих источников."""
    stmt = select(RelationModel).where(
        RelationModel.project_id == project_id, RelationModel.to_uid.is_(None)
    )
    if refs:
        parsed: list[EntityRef] = []
        problems: list[str] = []
        for raw in refs:
            try:
                parsed.append(parse_ref(raw))
            except RefSyntaxError as exc:
                problems.append(f"refs: {exc}")
        if problems:
            raise RelationError(problems)
        found = resolve_many(session, project_id, [r for r in parsed if r.kind in ENTITY_KINDS])
        stmt = stmt.where(RelationModel.from_uid.in_({h.uid for h in found.values()}))
    rows = list(session.scalars(stmt.order_by(RelationModel.row_id)))
    entity_ends = [
        EntityRef(RefKind(r.to_kind), r.to_key or "")
        for r in rows
        if RefKind(r.to_kind) in ENTITY_KINDS
    ]
    hits = resolve_many(session, project_id, entity_ends)
    report = VerifyReport()
    now = datetime.now(UTC)
    for row in rows:
        report.checked += 1
        kind = RefKind(row.to_kind)
        reason: str | None
        if kind in ENTITY_KINDS:
            hit = hits.get(EntityRef(kind, row.to_key or ""))
            reason = None if hit else "target_not_found"
            if hit is not None:
                _fill(session, row, hit.uid)
                report.filled += 1
                continue
        else:
            reason = _check_literal(session, project_id, row)
        row.resolved, row.broken_reason, row.last_checked = reason is None, reason, now
        if reason is None:
            report.resolved += 1
        else:
            report.broken.append(
                f"{row.from_kind}:{row.from_uid} {row.type} {row.to_kind}:{row.to_key}"
            )
    session.flush()
    return report


def _fill(session: Session, row: RelationModel, to_uid: str) -> None:
    """Дописать `to_uid` ссылке вперёд; дубль уже существующего ребра удаляется."""
    twin = session.scalars(
        select(RelationModel).where(
            RelationModel.project_id == row.project_id,
            RelationModel.from_uid == row.from_uid,
            RelationModel.type == row.type,
            RelationModel.to_kind == row.to_kind,
            RelationModel.to_uid == to_uid,
        )
    ).first()
    if twin is not None:
        session.delete(row)
    else:
        row.to_uid, row.to_key = to_uid, None
        row.resolved, row.broken_reason, row.last_checked = True, None, datetime.now(UTC)
