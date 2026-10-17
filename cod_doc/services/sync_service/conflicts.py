"""Конфликты слияния: открыть, перечислить, разрешить (RFC 29 §3.7, §10.6; CLO-017).

Конфликт — строка ``sync_conflict`` (local, не уезжает), а не маркеры в
тексте. Открывает его слияние (``merge``) в транзакции применения чужого
changeset'а: в БД остаётся «наше», «их» лежит в конфликте. Значения хранятся
каноническим JSON в форме changeset'а (§10.4): FK — uid конца, время — строка
§10.4, так что круг ``open → list → resolve`` не теряет типов.

**Разрешение — обычная запись** (§3.7, ADO-040), одной транзакцией:

1. значение (``ours``, ``theirs`` или свой текст) ложится в сущность через
   ORM — захват ``sync_capture`` пишет новую операцию с новым штампом HLC, и
   она уезжает следующим push'ем. Если значение уже в БД (обычно «наше»),
   ORM изменения не увидит, и операция пишется явно ``sync_capture.record``:
   штамп «нашей» правки мог проиграть LWW на других репликах, а свежий —
   больше любого увиденного;
2. ревизия сущности — если у таблицы есть ``EntityKind`` (адрес ревизии —
   пара ``(entity_kind, entity_id)``); у остальных authored-таблиц ревизий нет
   и сегодня, там только событие — как ``emit_for_write`` у прочих сервисов;
3. событие ``sync.conflict_resolved`` (§10.11): ``scope_kind`` — таблица
   сущности, ``payload = {conflict_uid, kind, take}``; свой текст в событие
   не кладётся, ``take = "custom"``;
4. отметка ``status = resolved``, ``resolved_by``, ``resolved_at``.

**Коды** (§10.10). ``SYNC-302`` — конфликта нет в проекте или он уже
разрешён; ``SYNC-301`` — ``take`` не подходит к виду: свой текст — только у
``text``, у остальных видов — ``ours``/``theirs``. Проверки идут до первой
записи. Виды со снимком поддерева и склейкой (``delete_edit``, ``key_merge``,
``double_checkout``) разрешаются механикой своих задач (CLO-022, CLO-023,
CLO-031) — до них ``MergeRequiredError`` с номером задачи, тоже до записи.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal, cast

from sqlalchemy import inspect, select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.models import ProjectModel, SyncConflictModel
from cod_doc.infra.models.sync import SYNC_CONFLICT_KINDS
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local, apply
from cod_doc.services.sync_service._values import to_wire
from cod_doc.services.sync_service.changeset import FieldValue, canonical_json
from cod_doc.services.sync_service.errors import (
    ConflictNotFoundError,
    ConflictResolveError,
    MergeRequiredError,
)
from cod_doc.services.sync_service.merge import ConflictSpec

if TYPE_CHECKING:
    from sqlalchemy.orm import InstanceState, Session

__all__ = [
    "KINDS",
    "ConflictSpec",
    "list_conflicts",
    "open_conflict",
    "resolve_conflict",
]

KINDS: Final = frozenset(SYNC_CONFLICT_KINDS)
OPEN: Final = "open"
RESOLVED: Final = "resolved"
_STATUS_FILTERS: Final = frozenset({OPEN, RESOLVED, "all"})
_OURS: Final = "ours"
_THEIRS: Final = "theirs"
_CUSTOM: Final = "custom"
#: Виды, у которых разрешение — запись одного поля сущности.
FIELD_KINDS: Final = frozenset({"field", "text", "status"})
#: Виды со своей механикой разрешения → задача, которая её строит.
DEFERRED_KINDS: Final = {
    "delete_edit": "CLO-022",
    "key_merge": "CLO-023",
    "double_checkout": "CLO-031",
}
_ENTITY_KINDS: Final = {table: kind for kind, table in reg.ENTITY_KIND_TABLES.items()}
_UUID7: Final = 7
#: Сдвиг до 48 бит времени в начале uuid7: 128 − 48.
_UUID7_MS_SHIFT: Final = 80
_MS_PER_S: Final = 1000


def _encode(value: FieldValue) -> str:
    return canonical_json(value).decode("utf-8")


def _decode(raw: str | None) -> object:
    return None if raw is None else json.loads(raw)


def open_conflict(session: Session, project_id: int, spec: ConflictSpec, *, author: str) -> str:
    """Записать открытый конфликт и событие ``sync.conflict_opened``; вернуть uid."""
    uid = str(uuid.uuid7())
    session.add(
        SyncConflictModel(
            uid=uid,
            project_id=project_id,
            entity_kind=spec.entity_kind,
            entity_uid=spec.entity_uid,
            field=spec.field,
            kind=spec.kind,
            ours=_encode(spec.ours),
            theirs=_encode(spec.theirs),
            base=_encode(spec.base),
            theirs_op=spec.theirs_op,
            status=OPEN,
        )
    )
    session.flush()
    activity_service.emit_for_write(
        session,
        project_id,
        "sync.conflict_opened",
        author,
        scope_kind=spec.entity_kind,
        scope_id=spec.entity_uid,
        payload={"conflict_uid": uid, "kind": spec.kind},
        summary=f"конфликт {spec.kind}: {spec.entity_kind} {spec.entity_uid} {spec.field or ''}",
    )
    return uid


def _created(uid: str) -> str | None:
    """Момент открытия из uid (uuid7 несёт миллисекунды Unix, RFC 9562)."""
    try:
        parsed = uuid.UUID(uid)
    except ValueError:
        return None
    if parsed.version != _UUID7:
        return None
    return datetime.fromtimestamp((parsed.int >> _UUID7_MS_SHIFT) / _MS_PER_S, UTC).isoformat()


def _as_dict(row: SyncConflictModel) -> dict[str, object]:
    return {
        "uid": row.uid,
        "created": _created(row.uid),
        "entity_kind": row.entity_kind,
        "entity_uid": row.entity_uid,
        "field": row.field,
        "kind": row.kind,
        "ours": _decode(row.ours),
        "theirs": _decode(row.theirs),
        "base": _decode(row.base),
        "theirs_op": row.theirs_op,
        "status": row.status,
        "resolved_by": row.resolved_by,
        "resolved_at": None if row.resolved_at is None else row.resolved_at.isoformat(),
    }


def list_conflicts(
    session: Session, project_id: int, *, status: str = OPEN
) -> list[dict[str, object]]:
    """Конфликты проекта по ``status`` (``open`` | ``resolved`` | ``all``), старые раньше.

    uid — uuid7, поэтому порядок по uid — порядок открытия.
    """
    if status not in _STATUS_FILTERS:
        msg = f"status {status!r} не из {sorted(_STATUS_FILTERS)}"
        raise ValueError(msg)
    stmt = select(SyncConflictModel).where(SyncConflictModel.project_id == project_id)
    if status != "all":
        stmt = stmt.where(SyncConflictModel.status == status)
    return [_as_dict(row) for row in session.scalars(stmt.order_by(SyncConflictModel.uid))]


def count_open(session: Session, project_id: int) -> int:
    """Число открытых конфликтов проекта (``sync status``)."""
    return len(
        session.scalars(
            select(SyncConflictModel.uid).where(
                SyncConflictModel.project_id == project_id, SyncConflictModel.status == OPEN
            )
        ).all()
    )


def _open_row(session: Session, project_id: int, conflict_uid: str) -> SyncConflictModel:
    row = session.get(SyncConflictModel, conflict_uid)
    if row is None or row.project_id != project_id:
        msg = f"конфликта {conflict_uid} в проекте нет"
        raise ConflictNotFoundError(msg)
    if row.status != OPEN:
        msg = f"конфликт {conflict_uid} уже разрешён ({row.resolved_by})"
        raise ConflictNotFoundError(msg)
    return row


def _chosen(row: SyncConflictModel, take: str) -> tuple[object, str]:
    """(значение, метка take) или SYNC-301/граница задачи — до первой записи."""
    if take in (_OURS, _THEIRS):
        if row.kind in DEFERRED_KINDS:
            msg = f"разрешение конфликта {row.kind}"
            raise MergeRequiredError(msg, task=DEFERRED_KINDS[row.kind])
        return _decode(row.ours if take == _OURS else row.theirs), take
    if row.kind != "text":
        msg = f"конфликт {row.kind}: take — ours или theirs, свой текст только у text"
        raise ConflictResolveError(msg)
    return take, _CUSTOM


def _project(session: Session, project_id: int) -> _local.ProjectRef:
    proj = session.get_one(ProjectModel, project_id)
    return _local.ProjectRef(row_id=proj.row_id, uid=str(proj.uid), slug=proj.slug)


def _write_value(
    session: Session, project: _local.ProjectRef, row: SyncConflictModel, value: object, author: str
) -> tuple[int, object]:
    """Записать поле сущности через ORM; вернуть (row_id, прежнее значение)."""
    column = cast("str", row.field)
    target = apply.entity_by_uid(session, project, row.entity_kind, row.entity_uid)
    if target is None:
        msg = f"конфликт {row.uid}: {row.entity_kind} {row.entity_uid} в проекте больше нет"
        raise ConflictNotFoundError(msg)
    obj, info = target
    if column not in info.shipped:
        msg = f"конфликт {row.uid}: колонка {row.entity_kind}.{column} не уезжает"
        raise ConflictResolveError(msg)
    attr = info.attr(column)
    previous = getattr(obj, attr)
    values = apply.orm_values(session, project, info, {column: value}, obj)
    setattr(obj, attr, values[column])
    state = cast("InstanceState[object]", inspect(obj))
    changed = state.attrs[attr].history.has_changes()
    session.flush()
    if not changed:
        # Значение уже в БД: ORM операции не даст, а её штамп нужен свежий.
        wire = to_wire(row.entity_kind, {column: value})
        cap.record(
            session,
            project_id=project.row_id,
            table=row.entity_kind,
            entity_uid=row.entity_uid,
            kind="upsert",
            fields=wire,
            base=wire,
            author=author,
        )
    return int(getattr(obj, "row_id")), previous  # noqa: B009


def resolve_conflict(
    session: Session,
    project_id: int,
    conflict_uid: str,
    *,
    take: Literal["ours", "theirs"] | str,
    author: str,
    reason: str | None = None,
) -> dict[str, object]:
    """Разрешить открытый конфликт: значение, операция, ревизия, событие — одна транзакция.

    Возвращает конфликт после разрешения и ``revision_id`` записанной ревизии
    (``None`` у таблиц без ``EntityKind``) — ответ ``sync_resolve`` (§10.7).
    """
    row = _open_row(session, project_id, conflict_uid)
    value, label = _chosen(row, take)
    project = _project(session, project_id)
    cap.set_author(session, author)
    entity_id, previous = _write_value(session, project, row, value, author)
    payload: dict[str, object] = {"conflict_uid": row.uid, "kind": row.kind, "take": label}
    summary = f"конфликт {row.kind} {row.entity_kind}.{row.field}: {label}"
    entity_kind = _ENTITY_KINDS.get(row.entity_kind)
    revision_id: str | None = None
    if entity_kind is not None:
        diff = {
            "op": "sync.resolve",
            "field": row.field,
            "take": label,
            "old": _jsonable(previous),
            "new": _jsonable(value),
        }
        revision, _event = activity_service.write_revision_and_emit_event(
            session,
            project_id=project_id,
            entity_kind=EntityKind(entity_kind),
            entity_id=entity_id,
            author=author,
            diff=json.dumps(diff, ensure_ascii=False, default=str),
            reason=reason or f"sync resolve --take {label}",
            activity_kind="sync.conflict_resolved",
            activity_scope_kind=row.entity_kind,
            activity_scope_id=row.entity_uid,
            activity_payload=payload,
            activity_summary=summary,
        )
        revision_id = revision.revision_id
    else:
        activity_service.emit_for_write(
            session,
            project_id,
            "sync.conflict_resolved",
            author,
            scope_kind=row.entity_kind,
            scope_id=row.entity_uid,
            payload=payload,
            summary=summary,
        )
    row.status = RESOLVED
    row.resolved_by = author
    row.resolved_at = datetime.now(UTC)
    session.flush()
    return {**_as_dict(row), "revision_id": revision_id}


def _jsonable(value: object) -> object:
    return value.value if hasattr(value, "value") and not isinstance(value, str) else value
