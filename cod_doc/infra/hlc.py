"""Гибридные логические часы и идентичность реплики (RFC 29 §3.4, §10.1; CLO-005).

Штамп — строка ``'<ms:013d>.<counter:04d>.<replica_uid>'``: физическое время в
миллисекундах, счётчик событий внутри одной миллисекунды и uid реплики для
тай-брейка. Поля фиксированной ширины, поэтому строки сортируются так же, как
штампы: ``sync_op.hlc`` можно упорядочивать обычным ``ORDER BY``.

Монотонность. Часы машины могут уйти назад (NTP, ручная правка, сон ноутбука).
Штамп при этом не уменьшается: пока стенное время не обогнало последний
выданный штамп, растёт счётчик; переполненный счётчик занимает следующую
миллисекунду. Между процессами монотонность держит засев: ``clock_for`` при
первом обращении к реплике в процессе читает максимальный записанный штамп
этой реплики из ``sync_op``.

Ограничение: два процесса, пишущие от одной реплики (MCP-демон и CLI), каждый
держат свои часы и засеваются один раз. Их штампы могут совпасть до
миллисекунды и счётчика — порядок между такими операциями задаёт ``op_id``
(ULID), а ``sync_op.op_id`` — первичный ключ, так что совпадение штампов
ничего не ломает.

Реплика (``replica``) — состояние машины, а не проекта (``local`` в реестре).
``self_replica`` создаёт ровно одну строку ``is_self = 1`` на проект; гонку двух
процессов разрешает ``uq_replica_self`` (``INSERT … ON CONFLICT DO NOTHING`` и
повторное чтение).
"""

from __future__ import annotations

import getpass
import re
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, bindparam, text

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Connection
    from sqlalchemy.orm import Session

#: Счётчик — четыре десятичных знака формата §10.1.
MAX_COUNTER = 9999
#: Миллисекунды — тринадцать знаков: хватает до 2286 года.
_MS_WIDTH = 13
_MAX_MS = 10**_MS_WIDTH - 1
_HLC_RE = re.compile(r"(\d{13})\.(\d{4})\.(.+)")


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


@dataclass(frozen=True, order=True, slots=True)
class Hlc:
    """Штамп HLC; порядок полей = порядок сравнения = порядок строк."""

    ms: int
    counter: int
    replica_uid: str

    def __post_init__(self) -> None:
        if not 0 <= self.ms <= _MAX_MS or not 0 <= self.counter <= MAX_COUNTER:
            msg = f"HLC вне формата §10.1: ms={self.ms}, counter={self.counter}"
            raise ValueError(msg)
        if not self.replica_uid:
            msg = "HLC без replica_uid"
            raise ValueError(msg)

    def __str__(self) -> str:
        return f"{self.ms:013d}.{self.counter:04d}.{self.replica_uid}"

    @classmethod
    def parse(cls, value: str) -> Hlc:
        match = _HLC_RE.fullmatch(value)
        if match is None:
            msg = f"не HLC формата §10.1: {value!r}"
            raise ValueError(msg)
        return cls(int(match[1]), int(match[2]), match[3])


class HybridClock:
    """HLC одной реплики. Потокобезопасны: MCP-демон пишет из нескольких потоков."""

    def __init__(
        self,
        replica_uid: str,
        *,
        wall_ms: Callable[[], int] = _wall_ms,
        last: Hlc | None = None,
    ) -> None:
        self.replica_uid = replica_uid
        self._wall = wall_ms
        self._ms = last.ms if last is not None else 0
        self._counter = last.counter if last is not None else -1
        self._lock = threading.Lock()

    def _advance(self, ms: int, counter: int) -> Hlc:
        if counter > MAX_COUNTER:
            ms, counter = ms + 1, 0
        self._ms, self._counter = ms, counter
        return Hlc(ms, counter, self.replica_uid)

    def now(self) -> Hlc:
        """Штамп локального события: не меньше стенного времени и больше прошлого."""
        with self._lock:
            wall = self._wall()
            if wall > self._ms:
                return self._advance(wall, 0)
            return self._advance(self._ms, self._counter + 1)

    def observe(self, remote: Hlc) -> Hlc:
        """Принять чужой штамп (pull): следующий свой штамп будет больше обоих."""
        with self._lock:
            wall = self._wall()
            ms = max(wall, self._ms, remote.ms)
            if ms == self._ms == remote.ms:
                counter = max(self._counter, remote.counter) + 1
            elif ms == self._ms:
                counter = self._counter + 1
            elif ms == remote.ms:
                counter = remote.counter + 1
            else:
                counter = 0
            return self._advance(ms, counter)


_CLOCKS: dict[str, HybridClock] = {}
_CLOCKS_LOCK = threading.Lock()


def clock_for(session: Session | Connection, replica_uid: str) -> HybridClock:
    """Часы реплики в этом процессе; при первом обращении засеваются из журнала."""
    with _CLOCKS_LOCK:
        clock = _CLOCKS.get(replica_uid)
        if clock is not None:
            return clock
    stored = session.execute(
        text("SELECT max(hlc) FROM sync_op WHERE replica_uid = :r"), {"r": replica_uid}
    ).scalar_one_or_none()
    last = Hlc.parse(stored) if stored else None
    with _CLOCKS_LOCK:
        return _CLOCKS.setdefault(replica_uid, HybridClock(replica_uid, last=last))


def _default_label() -> str:
    try:
        user = getpass.getuser()
    except KeyError, OSError:
        user = "unknown"
    return f"{user}@{socket.gethostname()}"


#: Типы связок — как у ORM-колонок: без них sqlite3 берёт устаревший
#: адаптер datetime, а Postgres получил бы ``1`` вместо ``true``.
_SELECT_SELF = text("SELECT uid FROM replica WHERE project_id = :p AND is_self = :t").bindparams(
    bindparam("t", type_=Boolean)
)
_INSERT_SELF = text(
    "INSERT INTO replica (uid, project_id, is_self, label, created) "
    "VALUES (:uid, :p, :t, :label, :created) ON CONFLICT DO NOTHING"
).bindparams(bindparam("t", type_=Boolean), bindparam("created", type_=DateTime(timezone=True)))


def self_replica(
    session: Session | Connection, project_id: int, *, label: str | None = None
) -> str:
    """uid своей реплики проекта; создаёт её (``is_self = 1``) при первом вызове.

    Core-запросы, а не ORM: функцию зовёт хук захвата из ``before_commit``, где
    новый ORM-объект ушёл бы в следующий flush, а не в эту транзакцию.
    """
    params = {"p": project_id, "t": True}
    found = session.execute(_SELECT_SELF, params).scalar_one_or_none()
    if found is not None:
        return str(found)
    session.execute(
        _INSERT_SELF,
        {
            **params,
            "uid": str(uuid.uuid7()),
            "label": label or _default_label(),
            "created": datetime.now(UTC),
        },
    )
    return str(session.execute(_SELECT_SELF, params).scalar_one())
