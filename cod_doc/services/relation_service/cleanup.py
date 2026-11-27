"""Рёбра удалённой сущности (RFC 39 §3.5): подписчик `EntityDeleted`.

FK на концах нет, поэтому каскада нет: без этого обработчика ребро повисло бы
на `uid` несуществующей строки. Снимаются рёбра обоих направлений; каждое
снятие — `relation.removed` с причиной `endpoint_deleted`. Если удалён
источник, ревизию писать некуда — остаётся событие; если цель — ревизия
пишется на пережившего источника, как при явном `unrelate`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select

from cod_doc.domain.relations import EntityRef, RefKind
from cod_doc.infra.models import RelationModel
from cod_doc.services import entity_events
from cod_doc.services.entity_ref import describe

from .edges import record

if TYPE_CHECKING:
    from cod_doc.services.entity_events import EntityDeleted

REASON = "endpoint_deleted"


def on_entity_deleted(event: EntityDeleted) -> None:
    if not event.uid:
        return  # без uid на строку не могло быть рёбер
    session = event.session
    rows = list(
        session.scalars(
            select(RelationModel)
            .where(
                RelationModel.project_id == event.project_id,
                or_(RelationModel.from_uid == event.uid, RelationModel.to_uid == event.uid),
            )
            .order_by(RelationModel.row_id)
        )
    )
    if not rows:
        return
    deleted = EntityRef(event.kind, event.key or event.uid)
    wanted = {
        (RefKind(r.to_kind if r.from_uid == event.uid else r.from_kind), uid)
        for r in rows
        if (uid := r.to_uid if r.from_uid == event.uid else r.from_uid) is not None
    }
    others = describe(session, event.project_id, wanted)
    for row in rows:
        source_deleted = row.from_uid == event.uid
        other_uid = row.to_uid if source_deleted else row.from_uid
        other_kind = RefKind(row.to_kind if source_deleted else row.from_kind)
        hit = others.get(other_uid) if other_uid else None
        other = EntityRef(other_kind, hit.key if hit else (row.to_key or other_uid or "?"))
        from_ref, to_ref = (deleted, other) if source_deleted else (other, deleted)
        info = (row.type, row.from_uid, row.to_uid, row.note)
        session.delete(row)
        if event.author is not None:
            record(
                session,
                project_id=event.project_id,
                op="removed",
                author=event.author,
                type_name=info[0],
                source=None if source_deleted else hit,
                from_ref=from_ref,
                from_uid=info[1],
                to_ref=to_ref,
                to_uid=info[2],
                note=info[3],
                reason=REASON,
            )
    session.flush()


def register() -> None:
    entity_events.subscribe(entity_events.EntityDeleted, on_entity_deleted)
