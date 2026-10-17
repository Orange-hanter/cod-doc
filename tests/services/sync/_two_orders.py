"""Одни офлайн-правки, два порядка синхронизации — обвязка CLO-019…021.

Сценарий каждого теста слияния: реплики A и B совпадают, обе правят офлайн,
дальше картина копируется в отдельный каталог (обе БД и remote) и сводится
в двух «вселенных»: в первой первой пушит A, во второй — B. Сравниваются
дайджесты внутри вселенной и между ними, и набор открытых конфликтов.

Конфликт — local-строка: его открывает та реплика, которая сливала свой хвост
с чужим changeset'ом, то есть второй по порядку pull (SCN-089: «после pull на
B создан ровно один sync_conflict»). Поэтому uid конфликта у реплик не общий
(uuid7), а сравнивается подпись ``(entity_kind, entity_uid, field, kind)``.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.remote.file import FileRemote
from cod_doc.services.sync_service import conflicts, digest, pull, push
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

TICK = 0.003  # больше миллисекунды HLC

type Signature = tuple[str, str, str | None, str]


@dataclass
class Universe:
    a: r.Replica
    b: r.Replica
    store: FileRemote
    #: Кто пушил первым: ``"a"`` или ``"b"``.
    first: str

    @property
    def second(self) -> r.Replica:
        return self.b if self.first == "a" else self.a

    @property
    def replicas(self) -> tuple[r.Replica, r.Replica]:
        return self.a, self.b


def push_(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def pull_(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def exchange(u: Universe) -> pull.PullResult:
    """first push → second pull + push → first pull; итог — pull второго."""
    first, second = (u.a, u.b) if u.first == "a" else (u.b, u.a)
    push_(first, u.store)
    merged = pull_(second, u.store)
    push_(second, u.store)
    pull_(first, u.store)
    return merged


def settle(u: Universe) -> None:
    """Довезти хвосты после разрешения: второй push → первый pull."""
    first, second = (u.a, u.b) if u.first == "a" else (u.b, u.a)
    push_(second, u.store)
    pull_(first, u.store)
    push_(first, u.store)
    pull_(second, u.store)


def _copy(tmp: Path, a: r.Replica, b: r.Replica, src: FileRemote) -> tuple:
    tmp.mkdir()
    copies = []
    for rep in (a, b):
        path = tmp / rep.db_path.name
        with sqlite3.connect(rep.db_path) as from_, sqlite3.connect(path) as to:
            from_.backup(to)
        engine = make_engine(f"sqlite:///{path}")
        copies.append(replace(rep, sf=make_session_factory(engine), engine=engine, db_path=path))
    shutil.copytree(src.root, tmp / "remote")
    return copies[0], copies[1], FileRemote(tmp / "remote")


def two_orders(
    tmp_path: Path,
    setup: Callable[[r.Replica, r.Replica, FileRemote], None],
    edits: Callable[[r.Replica, r.Replica], None],
) -> tuple[Universe, Universe]:
    """Общая история ``setup`` (уже синхронна), офлайн-правки ``edits``, две вселенные."""
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    setup(a, b, store)
    push_(a, store)
    pull_(b, store)
    push_(b, store)
    pull_(a, store)
    edits(a, b)
    a2, b2, store2 = _copy(tmp_path / "b-first", a, b, store)
    return Universe(a, b, store, "a"), Universe(a2, b2, store2, "b")


def tick() -> None:
    time.sleep(TICK)


def digest_of(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return digest.compute(s, rep.project_id)


def authored_digest_of(rep: r.Replica) -> str:
    """Дайджест без journal-таблиц — для сравнения МЕЖДУ вселенными (CLO-024).

    Разрешение конфликта — новая запись человека в каждой вселенной: ревизия
    получает свой ``revision_id``, и состав журнала у вселенных законно разный.
    Внутри вселенной ревизии сходятся — там сравнивается полный ``digest_of``.
    """
    h = hashlib.sha256()
    with transactional(rep.sf, commit=False) as s:
        for table, uid, fields in cap.shipped_state(s, rep.project_id):
            if table not in reg.JOURNAL_IDS:
                h.update(digest._line(table, uid, fields))
    return h.hexdigest()


def open_conflicts(rep: r.Replica) -> list[dict[str, object]]:
    with transactional(rep.sf, commit=False) as s:
        return conflicts.list_conflicts(s, rep.project_id)


def signatures(rep: r.Replica) -> set[Signature]:
    return {
        (str(c["entity_kind"]), str(c["entity_uid"]), c["field"], str(c["kind"]))  # type: ignore[misc]
        for c in open_conflicts(rep)
    }


def resolve_all(rep: r.Replica, take: Callable[[dict[str, object]], str]) -> None:
    with transactional(rep.sf) as s:
        for row in conflicts.list_conflicts(s, rep.project_id):
            conflicts.resolve_conflict(
                s, rep.project_id, str(row["uid"]), take=take(row), author=r.AUTHOR
            )


def assert_converged(u: Universe) -> None:
    for rep in u.replicas:
        assert r.pending(rep) == 0, f"{rep.name}: хвост не уехал"
        assert digest.verify(rep.sf, r.SLUG, store=u.store).equal
    assert r.authored_state(u.a) == r.authored_state(u.b)
    assert digest_of(u.a) == digest_of(u.b)


def dispose(*universes: Universe) -> None:
    for u in universes:
        for rep in u.replicas:
            rep.engine.dispose()
