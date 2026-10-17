"""Значения колонок на проводе: время в форме §10.4 и обратно (RFC 29 §10.4).

Захват (``infra/sync_capture``) пишет время в ``sync_op.fields`` через
``isoformat()``: ``+00:00`` вместо ``Z``, микросекунды пропадают при нуле. В
журнале время — уже строка, и ``changeset`` его как время не узнаёт. Чинится в
одном месте — здесь, при упаковке, по типу колонки: так же нормализуются и
операции, записанные до этой правки. Захват при этом не трогается: журнал —
local, его форма ни с кем не сверяется, а форма changeset'а — контракт.

Обратно (применение) строка §10.4 становится ``datetime`` с UTC, ``date`` —
``date``: ORM-тип ``DateTime`` на SQLite строку не принимает.
"""

from __future__ import annotations

import functools
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import Date, DateTime

from cod_doc.infra.models import Base
from cod_doc.services.sync_service.changeset import FieldValue, format_time

if TYPE_CHECKING:
    from collections.abc import Mapping


@functools.cache
def _time_columns(table: str) -> tuple[frozenset[str], frozenset[str]]:
    """(колонки ``DateTime``, колонки ``Date``) таблицы; незнакомая — пусто."""
    tbl = Base.metadata.tables.get(table)
    if tbl is None:
        return frozenset(), frozenset()
    stamps = frozenset(c.name for c in tbl.columns if isinstance(c.type, DateTime))
    days = frozenset(c.name for c in tbl.columns if isinstance(c.type, Date))
    return stamps, days


def _parse_stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def to_wire(table: str, payload: Mapping[str, object]) -> dict[str, FieldValue]:
    """Поля операции из журнала → форма changeset'а: время по §10.4."""
    stamps, _ = _time_columns(table)
    out: dict[str, FieldValue] = {}
    for column, value in payload.items():
        if column in stamps and isinstance(value, str):
            out[column] = format_time(_parse_stamp(value))
        else:
            # Из json.loads журнала: JSON-значение, то есть FieldValue.
            out[column] = cast("FieldValue", value)
    return out


def from_wire(table: str, column: str, value: object) -> object:
    """Значение колонки из changeset'а → значение для ORM-атрибута."""
    if not isinstance(value, str):
        return value
    stamps, days = _time_columns(table)
    if column in stamps:
        return _parse_stamp(value)
    if column in days:
        return date.fromisoformat(value)
    return value
