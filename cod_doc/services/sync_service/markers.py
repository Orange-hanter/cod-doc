"""Маркер разрешения конфликта: решение одной реплики закрывает конфликт на всех (CLO-045).

``sync_conflict`` — local: конфликт с одной подписью ``(entity_kind,
entity_uid, field, kind)`` может быть открыт на нескольких репликах, у каждой
со своим uid. Разрешение на одной (``conflicts.resolve_conflict``) пишет
значение со свежим штампом — оно выигрывает везде, — но без маркера конфликт
на остальных висел бы до ручного resolve на каждой (F4 аудита секции C).

**Представление** — операция журнала с зарезервированным видом сущности
``sync_resolution`` (``MARKER_KIND``), а не новый вид операции и не таблица:

- формат changeset v1 не меняется — у операции те же ключи (``op_id``,
  ``hlc``, ``entity_kind``, ``entity_uid``, ``kind = upsert``, ``fields``,
  ``base = {}``, ``author``), байты существующих changeset'ов и golden-sha те
  же, ``decode`` прежний; таблицы с таким именем в схеме нет (гейт в тестах);
- ``entity_uid`` — uid маркера (uuid7), ``fields`` — подпись:
  ``{"entity_kind", "entity_uid", "field", "kind"}``; разрешившая реплика —
  реплика штампа, время разрешения — ``hlc`` операции;
- маркер живёт в ``sync_op`` как любая операция: свой — хвостом до push, чужой
  — историей после применения. Отдельной таблицы нет, миграция нужна только
  под ``sync_conflict.opened_hlc``.

**Применение** (``close``) закрывает свои открытые конфликты с той же подписью,
открытые раньше маркера: ``opened_hlc < hlc`` маркера (``NULL`` — конфликт до
CLO-045, раньше любого маркера). ``resolved_by = sync:remote:<uid маркера>``,
событие ``sync.conflict_resolved`` с ``take = remote``. Конфликт, открытый
позже маркера, остаётся открытым; конфликт, открытый позже прихода маркера, но
о записях старше него, не открывается вовсе (``covered``) — так множество
открытых конфликтов не зависит от того, что пришло раньше.

``opened_hlc`` — старший штамп записей, сошедшихся в конфликте, а не часы
реплики в момент открытия: местное время открытия зависит от порядка доставки
(маркер мог ещё не доехать), штампы записей — нет.

**Недоверенный ввод** (``parse``) — как у остальных операций, плюс своё: только
``upsert`` с пустым ``base``, ровно четыре поля подписи, uid маркера — UUID, вид
конфликта из ``SYNC_CONFLICT_KINDS``, поле есть ровно у видов про поле и
уезжает у таблицы (``SHIPPED_COLUMNS``), таблица захватывается и не журнал.
Проект и штампы проверяет ``apply`` (changeset чужого проекта и штамп чужой
реплики или из будущего не доходят до применения); restricted-строку и
придержанную колонку подписи — ``apply._require_shipped`` до ``close``.
Нарушение — SYNC-102.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import insert, inspect, select
from ulid import ULID

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.hlc import clock_for, self_replica
from cod_doc.infra.models import Base, SyncOpModel
from cod_doc.infra.models.sync import SYNC_CONFLICT_KINDS
from cod_doc.services.sync_service import _conflict_rows
from cod_doc.services.sync_service.errors import ObjectMissingError

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service._conflict_rows import Signature
    from cod_doc.services.sync_service.changeset import Op

__all__ = [
    "FIELD_KINDS",
    "MARKER_KIND",
    "Marker",
    "close",
    "covered",
    "latest",
    "parse",
    "record",
    "resolved_by",
]

#: Зарезервированный вид сущности операции-маркера (не таблица схемы).
MARKER_KIND: Final = "sync_resolution"
#: Виды конфликта про одно поле (у остальных ``field`` пуст).
FIELD_KINDS: Final = frozenset({"field", "text", "status"})
_FIELDS: Final = frozenset({"entity_kind", "entity_uid", "field", "kind"})
_UPSERT: Final = "upsert"
_REMOTE: Final = "sync:remote:"
#: Потолок длины uid сущности в подписи: uuid7/uuid5 — 36 символов.
_MAX_UID: Final = 64


@dataclass(frozen=True, slots=True)
class Marker:
    """Разобранный маркер: uid, штамп разрешения и подпись конфликта."""

    uid: str
    hlc: str
    signature: Signature


def resolved_by(marker_uid: str) -> str:
    return f"{_REMOTE}{marker_uid}"


def _dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload(signature: Signature) -> dict[str, object]:
    entity_kind, entity_uid, field, kind = signature
    return {"entity_kind": entity_kind, "entity_uid": entity_uid, "field": field, "kind": kind}


def record(session: Session, project_id: int, signature: Signature, *, author: str) -> str:
    """Записать свой маркер в хвост ``sync_op``; вернуть его uid. Уедет следующим push'ем."""
    replica = self_replica(session, project_id)
    marker_uid = str(uuid.uuid7())
    session.execute(
        insert(cast("Table", SyncOpModel.__table__)),
        [
            {
                "op_id": str(ULID()),
                "project_id": project_id,
                "replica_uid": replica,
                "hlc": str(clock_for(session, replica).now()),
                "entity_kind": MARKER_KIND,
                "entity_uid": marker_uid,
                "kind": _UPSERT,
                "fields": _dumps(_payload(signature)),
                "base": "{}",
                "author": author,
                "changeset": None,
            }
        ],
    )
    return marker_uid


def latest(session: Session, project_id: int, signature: Signature) -> str | None:
    """Штамп последнего маркера подписи, который знает реплика (свой хвост и история)."""
    rows = session.execute(
        select(SyncOpModel.hlc, SyncOpModel.fields).where(
            SyncOpModel.project_id == project_id, SyncOpModel.entity_kind == MARKER_KIND
        )
    ).all()
    want = _payload(signature)
    stamps = [hlc for hlc, raw in rows if json.loads(raw) == want]
    return max(stamps) if stamps else None


def covered(session: Session, project_id: int, signature: Signature, hlc: str | None) -> bool:
    """Конфликт о записях не новее ``hlc`` уже решён маркером этой подписи.

    Штамп записей неизвестен (``None``) — не решён: скрыть новый конфликт хуже,
    чем показать решённый.
    """
    last = latest(session, project_id, signature)
    return last is not None and hlc is not None and hlc < last


def _reject(op: Op, why: str) -> ObjectMissingError:
    return ObjectMissingError(f"маркер разрешения {op.entity_uid}: {why}")


def _shipped_columns(op: Op, table: str) -> frozenset[str]:
    for mapper in Base.registry.mappers:
        if cast("Table", mapper.local_table).name == table:
            info = cap.table_info(inspect(mapper))
            if info is not None:
                return frozenset(info.shipped)
    raise _reject(op, f"таблица {table!r} не уезжает")


def parse(op: Op) -> Marker:
    """Проверить форму маркера с remote (недоверенный ввод); SYNC-102 на нарушение."""
    if op.kind != _UPSERT or op.base:
        raise _reject(op, "маркер только дописывается — правка или удаление")
    try:
        uuid.UUID(op.entity_uid)
    except ValueError:
        raise _reject(op, "uid маркера не UUID") from None
    if set(op.fields) != _FIELDS:
        raise _reject(op, f"поля {sorted(op.fields)} ≠ {sorted(_FIELDS)}")
    table, entity_uid = op.fields["entity_kind"], op.fields["entity_uid"]
    field, kind = op.fields["field"], op.fields["kind"]
    if not isinstance(table, str) or table not in cap.CAPTURED_TABLES:
        raise _reject(op, f"таблица {table!r} не захватывается")
    if table in reg.JOURNAL_IDS:
        raise _reject(op, f"журнал {table!r} не сливается — конфликтов у него нет")
    if not isinstance(entity_uid, str) or not entity_uid or len(entity_uid) > _MAX_UID:
        raise _reject(op, "uid сущности подписи — непустая строка")
    if not isinstance(kind, str) or kind not in SYNC_CONFLICT_KINDS:
        raise _reject(op, f"вид конфликта {kind!r} не из {sorted(SYNC_CONFLICT_KINDS)}")
    if kind in FIELD_KINDS:
        if not isinstance(field, str) or field not in _shipped_columns(op, table) - {"uid"}:
            raise _reject(op, f"поле {field!r} у {table} не уезжает")
    elif field is not None:
        raise _reject(op, f"у конфликта {kind} поля нет, получено {field!r}")
    return Marker(uid=op.entity_uid, hlc=op.hlc, signature=(table, entity_uid, field, kind))


def close(session: Session, project_id: int, marker: Marker, *, author: str) -> list[str]:
    """Закрыть свои открытые конфликты подписи, открытые раньше маркера; вернуть их uid."""
    rows = [
        row
        for row in _conflict_rows.open_rows(session, project_id, marker.signature)
        if row.opened_hlc is None or row.opened_hlc < marker.hlc
    ]
    _conflict_rows.close(
        session,
        project_id,
        rows,
        resolved_by=resolved_by(marker.uid),
        take="remote",
        author=author,
        why=f"разрешён на другой реплике (маркер {marker.uid})",
        extra={"marker": marker.uid},
    )
    return [row.uid for row in rows]
