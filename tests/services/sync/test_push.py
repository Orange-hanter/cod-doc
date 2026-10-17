"""CLO-012: push с fast-forward через CAS (RFC 29 §3.6, §10.6).

Acceptance: «Упаковка неупакованных sync_op в changeset, has_objects/put_object,
cas_ref; отказ CAS → pull и повтор до 3 раз, дальше SYNC-103; сеть вне
транзакции; событие sync.pushed; тест на SCN-083».
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text

from cod_doc.infra.db import transactional
from cod_doc.infra.models import ActivityEventModel, ChangesetModel, ProjectModel, SyncOpModel
from cod_doc.infra.remote.file import FileRemote
from cod_doc.services.sync_service import pull, push
from cod_doc.services.sync_service.changeset import decode
from cod_doc.services.sync_service.errors import PushRejectedError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from cod_doc.infra.remote.base import RefValue

#: Время в канонической форме §10.4.
CANONICAL_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z")


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote, **kw: object) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store, **kw)  # type: ignore[arg-type]


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _project_uid(rep: r.Replica) -> str:
    with transactional(rep.sf) as s:
        return str(s.get_one(ProjectModel, rep.project_id).uid)


def _remote_head(store: FileRemote, rep: r.Replica) -> str | None:
    ref = store.get_ref(_project_uid(rep), "main")
    return None if ref is None else ref.sha


class _Spy(FileRemote):
    """FileRemote с крючком перед каждым ``cas_ref``."""

    def __init__(self, inner: FileRemote, before_cas: Callable[[int], None]) -> None:
        super().__init__(inner.root)
        self.before_cas = before_cas
        self.cas_calls = 0

    def cas_ref(
        self, project_uid: str, name: str, expected: RefValue | None, new_sha: str
    ) -> RefValue | None:
        self.cas_calls += 1
        self.before_cas(self.cas_calls)
        return super().cas_ref(project_uid, name, expected, new_sha)


# --- упаковка и fast-forward ------------------------------------------------------


def test_push_packs_the_tail_and_fast_forwards_main(pair: tuple) -> None:
    a, _b, store = pair
    r.make_task(a, "DEM-001")
    with transactional(a.sf) as s:
        tail = s.scalars(select(SyncOpModel.op_id).where(SyncOpModel.changeset.is_(None))).all()

    result = _push(a, store)

    assert result.retries == 0
    assert result.ops == len(tail) > 0
    assert result.changesets == [result.head]
    assert _remote_head(store, a) == result.head
    uid = _project_uid(a)
    assert result.head is not None
    cs = decode(store.get_object(uid, result.head), result.head)
    assert cs.parents == ()
    assert sorted(op.op_id for op in cs.ops) == sorted(tail)
    with transactional(a.sf) as s:
        marks = s.scalars(select(SyncOpModel.changeset).distinct()).all()
        row = s.get_one(ChangesetModel, result.head)
    assert marks == [result.head]
    assert row.applied_at is not None
    assert r.pending(a) == 0


def test_second_push_without_tail_is_noop_and_next_one_stacks(pair: tuple) -> None:
    a, _b, store = pair
    r.make_task(a, "DEM-001")
    first = _push(a, store)

    idle = _push(a, store)
    r.set_description(a, "DEM-001", "ещё")
    second = _push(a, store)

    assert idle.changesets == []
    assert idle.head == first.head
    assert second.head is not None
    cs = decode(store.get_object(_project_uid(a), second.head), second.head)
    assert list(cs.parents) == [first.head]
    assert _remote_head(store, a) == second.head


def test_push_emits_sync_pushed(pair: tuple) -> None:
    a, _b, store = pair
    r.make_task(a, "DEM-001")
    result = _push(a, store)

    with transactional(a.sf) as s:
        event = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "sync.pushed")
        ).one()
    assert event.actor_id == r.AUTHOR
    assert event.scope_kind == "project"
    assert event.payload == {
        "remote": "origin",
        "changesets": result.changesets,
        "ops": result.ops,
        "head": result.head,
    }


def test_packed_changeset_carries_canonical_times(pair: tuple) -> None:
    """Находка B1: захват пишет ``isoformat()``, changeset — форму §10.4."""
    a, b, store = pair
    r.make_task(a, "DEM-001")
    with transactional(a.sf) as s:
        # Операция, записанная захватом до правки: «+00:00» и без микросекунд.
        row = s.scalars(select(SyncOpModel).where(SyncOpModel.entity_kind == "task")).one()
        fields = json.loads(row.fields)
        fields["created"] = "2026-10-17T12:00:00+00:00"
        row.fields = json.dumps(fields)
    result = _push(a, store)
    assert result.head is not None

    cs = decode(store.get_object(_project_uid(a), result.head), result.head)
    stamps = [
        (op.entity_kind, col, value)
        for op in cs.ops
        for col, value in op.fields.items()
        if col in {"created", "last_updated", "updated", "completed_at"} and value is not None
    ]
    assert stamps
    assert all(CANONICAL_TIME.fullmatch(str(v)) for _, _, v in stamps), stamps
    task_op = next(op for op in cs.ops if op.entity_kind == "task")
    assert task_op.fields["created"] == "2026-10-17T12:00:00.000000Z"
    # Обратно в БД реплики B — то же время.
    _pull(b, store)
    with transactional(b.sf) as s:
        created = s.execute(text("SELECT created FROM task")).scalar_one()
    assert str(created).startswith("2026-10-17 12:00:00")


# --- SCN-083 и повторы ------------------------------------------------------------


def _at_common_head(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    r.make_task(a, "DEM-001")
    r.make_task(a, "DEM-002")
    _push(a, store)
    _pull(b, store)


def test_scn_083_push_that_missed_a_foreign_push_is_rejected(pair: tuple) -> None:
    a, b, store = pair
    _at_common_head(a, b, store)
    r.set_description(a, "DEM-001", "A")
    pushed_a = _push(a, store)
    r.set_description(b, "DEM-002", "B")

    with pytest.raises(PushRejectedError) as err:
        _push(b, store, max_retries=0)  # cod-doc push --no-retry

    assert err.value.code == "SYNC-103"
    assert "remote ушёл вперёд — сделайте pull" in str(err.value)
    assert _remote_head(store, a) == pushed_a.head
    assert r.pending(b) > 0  # хвост B остался хвостом


def test_cas_rejection_pulls_and_retries(pair: tuple) -> None:
    a, b, store = pair
    _at_common_head(a, b, store)
    r.set_description(b, "DEM-002", "B")

    def race(call: int) -> None:
        if call == 1:  # A успевает запушить между упаковкой и CAS реплики B
            r.set_description(a, "DEM-001", "A")
            _push(a, store)

    spy = _Spy(store, race)
    result = push.push(b.sf, r.SLUG, author=r.AUTHOR, store=spy)

    assert result.retries == 1
    assert spy.cas_calls == 2
    assert _remote_head(store, a) == result.head
    assert r.task_description(b, "DEM-001") == "A"  # pull перед повтором
    assert result.head is not None
    cs = decode(store.get_object(_project_uid(b), result.head), result.head)
    head_a = _pull(a, store).head
    assert head_a == result.head
    assert r.task_description(a, "DEM-002") == "B"
    assert len(cs.parents) == 1
    assert r.authored_state(a) == r.authored_state(b)


def test_sync_103_after_three_retries(pair: tuple) -> None:
    a, b, store = pair
    _at_common_head(a, b, store)
    r.set_description(b, "DEM-002", "B")

    def race(call: int) -> None:  # remote уходит вперёд перед каждым CAS
        r.set_description(a, "DEM-001", f"A{call}")
        _push(a, store)

    spy = _Spy(store, race)
    with pytest.raises(PushRejectedError) as err:
        push.push(b.sf, r.SLUG, author=r.AUTHOR, store=spy)

    assert err.value.code == "SYNC-103"
    assert spy.cas_calls == push.DEFAULT_MAX_RETRIES + 1
    assert r.pending(b) > 0


def test_pull_that_needs_a_merge_ends_push_with_sync_103(pair: tuple) -> None:
    a, b, store = pair
    _at_common_head(a, b, store)
    r.set_description(a, "DEM-001", "A")
    _push(a, store)
    r.set_description(b, "DEM-001", "B")

    with pytest.raises(PushRejectedError, match="секция C"):
        _push(b, store)


# --- сеть вне транзакции ----------------------------------------------------------


class _LockProbe(FileRemote):
    """Каждый вызов транспорта проверяет, что БД реплики не держит запись."""

    def __init__(self, inner: FileRemote, db_path: Path) -> None:
        super().__init__(inner.root)
        self.db_path = db_path
        self.calls: list[str] = []

    def _probe(self, name: str) -> None:
        self.calls.append(name)
        conn = sqlite3.connect(self.db_path, timeout=0)
        try:
            conn.execute("BEGIN IMMEDIATE")  # упадёт «database is locked» под чужой записью
            conn.rollback()
        finally:
            conn.close()

    def get_ref(self, project_uid: str, name: str) -> RefValue | None:
        self._probe("get_ref")
        return super().get_ref(project_uid, name)

    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]:
        self._probe("has_objects")
        return super().has_objects(project_uid, shas)

    def put_object(self, project_uid: str, sha: str, data: bytes) -> None:
        self._probe("put_object")
        super().put_object(project_uid, sha, data)

    def get_object(self, project_uid: str, sha: str) -> bytes:
        self._probe("get_object")
        return super().get_object(project_uid, sha)

    def cas_ref(
        self, project_uid: str, name: str, expected: RefValue | None, new_sha: str
    ) -> RefValue | None:
        self._probe("cas_ref")
        return super().cas_ref(project_uid, name, expected, new_sha)


def test_network_runs_outside_db_transactions(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    probe_a = _LockProbe(store, a.db_path)
    push.push(a.sf, r.SLUG, author=r.AUTHOR, store=probe_a)
    probe_b = _LockProbe(store, b.db_path)
    pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=probe_b)

    assert {"get_ref", "has_objects", "put_object", "cas_ref"} <= set(probe_a.calls)
    assert {"get_ref", "get_object"} <= set(probe_b.calls)


class _AlwaysReject(FileRemote):
    """CAS всегда отвергнут, а remote при этом не уходит вперёд."""

    def __init__(self, inner: FileRemote) -> None:
        super().__init__(inner.root)
        self.cas_calls = 0

    def cas_ref(
        self, project_uid: str, name: str, expected: RefValue | None, new_sha: str
    ) -> RefValue | None:
        self.cas_calls += 1
        return None


def test_retry_stops_when_pull_does_not_move_the_head(pair: tuple) -> None:
    a, _b, store = pair
    r.make_task(a, "DEM-001")
    spy = _AlwaysReject(store)

    with pytest.raises(PushRejectedError, match="повтор бесполезен"):
        push.push(a.sf, r.SLUG, author=r.AUTHOR, store=spy)

    assert spy.cas_calls == 1
