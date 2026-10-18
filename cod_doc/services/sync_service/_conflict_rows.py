"""Открытые конфликты по подписи: найти и закрыть без человека (RFC 29 §3.7; CLO-044).

Подпись конфликта — ``(entity_kind, entity_uid, field, kind)``: uid у
``sync_conflict`` свой на каждой реплике (таблица local), а подпись у всех
одна. Закрывает автоматика тех случаев, где предмет конфликта исчез сам:
цепочка статуса больше не отвергает ни одной записи (``status_merge``).
Закрытие — та же отметка ``resolved``, что у ``resolve_conflict``, плюс
событие ``sync.conflict_resolved`` с ``take`` автоматики; записи в сущность
нет — значение уже решено слиянием.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from cod_doc.infra.models import SyncConflictModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

__all__ = ["OPEN", "RESOLVED", "Signature", "close", "open_rows", "signature_of"]

OPEN: Final = "open"
RESOLVED: Final = "resolved"

type Signature = tuple[str, str, str | None, str]


def signature_of(row: SyncConflictModel) -> Signature:
    return (row.entity_kind, row.entity_uid, row.field, row.kind)


def open_rows(session: Session, project_id: int, signature: Signature) -> list[SyncConflictModel]:
    """Открытые конфликты проекта с этой подписью, старые раньше."""
    entity_kind, entity_uid, field, kind = signature
    stmt = select(SyncConflictModel).where(
        SyncConflictModel.project_id == project_id,
        SyncConflictModel.status == OPEN,
        SyncConflictModel.entity_kind == entity_kind,
        SyncConflictModel.entity_uid == entity_uid,
        SyncConflictModel.kind == kind,
    )
    stmt = stmt.where(
        SyncConflictModel.field.is_(None) if field is None else SyncConflictModel.field == field
    )
    return list(session.scalars(stmt.order_by(SyncConflictModel.uid)))


def close(
    session: Session,
    project_id: int,
    rows: Sequence[SyncConflictModel],
    *,
    resolved_by: str,
    take: str,
    author: str,
    why: str,
    extra: dict[str, object] | None = None,
) -> None:
    """Закрыть конфликты без записи в сущность; событие на каждый (ADO-040)."""
    from cod_doc.services import activity_service

    for row in rows:
        row.status = RESOLVED
        row.resolved_by = resolved_by
        row.resolved_at = datetime.now(UTC)
        activity_service.emit_for_write(
            session,
            project_id,
            "sync.conflict_resolved",
            author,
            scope_kind=row.entity_kind,
            scope_id=row.entity_uid,
            payload={"conflict_uid": row.uid, "kind": row.kind, "take": take, **(extra or {})},
            summary=f"конфликт {row.kind} {row.entity_kind} {row.entity_uid}: {why}",
        )
    if rows:
        session.flush()
