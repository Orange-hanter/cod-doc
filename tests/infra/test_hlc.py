"""CLO-005: HLC формата RFC 29 §10.1 и идентичность реплики.

Acceptance: «HLC формата RFC 29 §10.1 монотонен при откате системных часов
назад; при init/clone создаётся ровно одна replica is_self=1 на проект
(uq_replica_self)».
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import insert, select, text
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.hlc import MAX_COUNTER, Hlc, HybridClock, clock_for, self_replica
from cod_doc.infra.models import ProjectModel, ReplicaModel, SyncOpModel
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_REPLICA = "0192f0c1-0000-7000-8000-000000000001"
#: RFC 29 §10.1: '<ms:013d>.<counter:04d>.<replica_uid>'.
_FORMAT = re.compile(r"^\d{13}\.\d{4}\..+$")


class _Wall:
    """Подменяемые системные часы в миллисекундах."""

    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


def test_format_matches_rfc() -> None:
    clock = HybridClock(_REPLICA, wall_ms=_Wall(1_760_000_000_123))
    stamp = str(clock.now())
    assert _FORMAT.fullmatch(stamp), stamp
    assert stamp == f"1760000000123.0000.{_REPLICA}"
    assert Hlc.parse(stamp) == Hlc(1_760_000_000_123, 0, _REPLICA)


def test_monotonic_when_wall_clock_goes_back() -> None:
    wall = _Wall(1_760_000_000_000)
    clock = HybridClock(_REPLICA, wall_ms=wall)
    stamps = [clock.now()]
    wall.ms -= 60_000  # NTP откатил часы на минуту
    stamps += [clock.now() for _ in range(5)]
    wall.ms += 30_000  # всё ещё позади последнего штампа
    stamps.append(clock.now())
    wall.ms += 120_000  # часы ушли вперёд — счётчик сбрасывается
    stamps.append(clock.now())

    strings = [str(s) for s in stamps]
    assert strings == sorted(strings), "строки HLC сортируются в порядке выдачи"
    assert len(set(strings)) == len(strings), "штампы не повторяются"
    assert stamps[-1].counter == 0
    assert stamps[-1].ms == wall.ms


def test_counter_overflow_borrows_a_millisecond() -> None:
    clock = HybridClock(_REPLICA, wall_ms=_Wall(1000), last=Hlc(5000, MAX_COUNTER, _REPLICA))
    nxt = clock.now()
    assert nxt == Hlc(5001, 0, _REPLICA)
    assert str(nxt) > str(Hlc(5000, MAX_COUNTER, _REPLICA))


def test_observe_moves_past_remote_stamp() -> None:
    clock = HybridClock(_REPLICA, wall_ms=_Wall(1000))
    remote = Hlc(9_000, 7, "0192f0c1-0000-7000-8000-00000000ffff")
    seen = clock.observe(remote)
    assert (seen.ms, seen.counter) == (9_000, 8)
    assert clock.now() > seen


def test_parse_rejects_garbage() -> None:
    for bad in ("", "1.2.x", "176000000012.0000.r", "1760000000123.000.r", "1760000000123.0000."):
        with pytest.raises(ValueError, match="HLC"):
            Hlc.parse(bad)


# --- реплика -----------------------------------------------------------------


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    url = f"sqlite:///{tmp_path / 'hlc.db'}"
    run_alembic("upgrade", "head", db_url=url)
    engine = make_engine(url)
    yield make_session_factory(engine)
    engine.dispose()


def _project(session: Session, slug: str) -> int:
    row = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    session.add(row)
    session.flush()
    return row.row_id


def test_self_replica_is_created_once_per_project(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        a = _project(session, "a")
        b = _project(session, "b")
        first = self_replica(session, a)
        assert self_replica(session, a) == first, "повторный вызов возвращает ту же реплику"
        other = self_replica(session, b)
        assert other != first
        session.commit()

    with factory() as session:
        assert self_replica(session, a) == first, "реплика переживает сессию"
        rows = session.execute(select(ReplicaModel.project_id, ReplicaModel.is_self)).all()
    assert sorted(rows) == [(a, True), (b, True)]


def test_uq_replica_self_rejects_second_self_row(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        pid = _project(session, "a")
        self_replica(session, pid)
        session.commit()
        with pytest.raises(IntegrityError):
            session.execute(
                insert(ReplicaModel).values(
                    uid="0192f0c1-0000-7000-8000-0000000000aa", project_id=pid, is_self=True
                )
            )
        session.rollback()
        # Чужие реплики (is_self = 0) индекс не ограничивает.
        session.execute(
            insert(ReplicaModel).values(
                uid="0192f0c1-0000-7000-8000-0000000000bb", project_id=pid, is_self=False
            )
        )
        session.commit()


def test_init_project_creates_self_replica(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cod_doc.config import ProjectEntry
    from cod_doc.infra.db import db_for_entry, dispose_cached_engines
    from cod_doc.services.project_service import init_project

    root = tmp_path / "proj"
    root.mkdir()
    entry = ProjectEntry(name="proj", path=str(root))
    init_project(entry)
    init_project(entry)  # идемпотентно: вторая реплика не появляется

    factory, _engine = db_for_entry(entry)
    with factory() as session:
        rows = session.execute(text("SELECT project_id, is_self FROM replica")).all()
    dispose_cached_engines()
    assert len(rows) == 1
    assert rows[0][1] == 1


def test_clock_for_seeds_from_journal(factory: sessionmaker[Session]) -> None:
    """Перезапуск процесса с отставшими часами не выдаёт штамп меньше записанного."""
    replica = "0192f0c1-0000-7000-8000-0000000000cc"
    future = Hlc(9_999_999_999_000, 3, replica)
    with factory() as session:
        pid = _project(session, "a")
        session.execute(
            insert(SyncOpModel).values(
                op_id="01J0000000000000000000000A",
                project_id=pid,
                replica_uid=replica,
                hlc=str(future),
                entity_kind="task",
                entity_uid="x",
                kind="upsert",
                fields="{}",
                base="{}",
                author="human:test",
            )
        )
        session.commit()
        clock = clock_for(session, replica)
        assert clock.now() > future
