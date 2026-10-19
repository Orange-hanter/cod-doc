"""CLO-032: эпохи схемы на remote и ``export-pending`` (RFC 29 §3.6, Q-012, §10.1).

Acceptance: «первый push новой эпохи пишет снапшот и поднимает
meta.epoch/schema_min (событие sync.epoch_opened); pull отставшей реплики —
SYNC-105, sync export-pending выгружает операции в файл».

Смена эпохи моделируется без настоящей миграции: реестр ``sync_epochs``
подменяется так, что голова цепочки (``0052``) открывает эпоху, а схема
реплики «до обновления» — запись ``0051`` в ``alembic_version`` при
физически той же схеме. Обновление реплики — запись головы обратно. Так
меняется ровно то, что читает код эпох: голова миграций БД.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, text

from cod_doc.infra import sync_epochs
from cod_doc.infra.db import transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel, SyncOpModel
from cod_doc.infra.remote.base import META_KEY
from cod_doc.services.sync_service import epochs, pull, push, snapshot, status
from cod_doc.services.sync_service.changeset import from_dict
from cod_doc.services.sync_service.errors import (
    EpochPassedError,
    ObjectMissingError,
    SchemaMismatchError,
)
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

OLD = "0051_sync_conflict"
NEW = "0052_sync_conflict_opened_hlc"
OLD_EPOCH = "0050_sync_rank"


@pytest.fixture(autouse=True)
def _head_opens_an_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sync_epochs,
        "MIGRATIONS",
        MappingProxyType({"0050_sync_rank": True, OLD: False, NEW: True}),
    )


@pytest.fixture
def world(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    for rep in (a, b):
        _set_schema(rep, OLD)
    return a, b, r.remote(tmp_path)


def _set_schema(rep: r.Replica, revision: str) -> None:
    with rep.engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": revision})


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _uid(rep: r.Replica) -> str:
    with transactional(rep.sf) as s:
        return str(s.get_one(ProjectModel, rep.project_id).uid)


def _meta(store: FileRemote, rep: r.Replica) -> dict[str, object]:
    raw = store.get_json(_uid(rep), META_KEY)
    assert raw is not None
    return dict(raw[0])


def _pending(rep: r.Replica) -> int:
    with transactional(rep.sf) as s:
        stmt = select(func.count()).select_from(SyncOpModel).where(SyncOpModel.changeset.is_(None))
        return int(s.scalar(stmt) or 0)


def _events(rep: r.Replica, kind: str) -> list[dict[str, object]]:
    with transactional(rep.sf) as s:
        rows = s.scalars(select(ActivityEventModel).where(ActivityEventModel.kind == kind))
        return [dict(row.payload) for row in rows]


def _synced(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    """A заводит remote в старой эпохе, B его догоняет."""
    _push(a, store)
    _pull(b, store)
    assert _meta(store, a)["epoch"] == OLD_EPOCH


def _open_new_epoch(a: r.Replica, store: FileRemote) -> push.PushResult:
    _set_schema(a, NEW)  # A обновилась
    r.make_task(a, "DEM-100")
    return _push(a, store)


# --- открытие эпохи ---------------------------------------------------------------


def test_push_on_old_schema_is_accepted_while_the_remote_epoch_is_unchanged(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    r.make_task(b, "DEM-001")
    result = _push(b, store)
    assert result.ops > 0
    assert result.epoch_opened is None
    assert _meta(store, a)["epoch"] == OLD_EPOCH


def test_first_push_of_a_new_epoch_writes_its_snapshot_and_raises_meta(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    result = _open_new_epoch(a, store)

    assert result.epoch_opened == NEW
    assert result.snapshot == result.head
    meta = _meta(store, a)
    assert meta["epoch"] == NEW
    assert meta["schema_min"] == NEW
    assert meta["snapshot"] == {"epoch": NEW, "sha": result.head, "ops": meta["snapshot"]["ops"]}  # type: ignore[index]
    pointer = snapshot.pointer(store, _uid(a))
    assert pointer is not None
    assert pointer[0].epoch == NEW
    data = store.get_snapshot(_uid(a), pointer[0])
    header, _project = snapshot.peek(data)
    assert (header.epoch, header.schema_head, header.head) == (NEW, NEW, result.head)
    assert _events(a, "sync.epoch_opened") == [{"epoch": NEW, "snapshot": result.head}]


def test_epoch_is_opened_once(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    _open_new_epoch(a, store)
    r.make_task(a, "DEM-101")
    again = _push(a, store)
    assert again.epoch_opened is None
    assert len(_events(a, "sync.epoch_opened")) == 1


# --- отставшая реплика ------------------------------------------------------------


def test_pull_by_a_replica_behind_the_remote_epoch_is_refused_with_sync_105(
    world: tuple,
) -> None:
    a, b, store = world
    _synced(a, b, store)
    _open_new_epoch(a, store)
    head_before = _pull_head(b)

    with pytest.raises(EpochPassedError, match=r"^SYNC-105: .*эпоху 0052.*cod-doc update"):
        _pull(b, store)
    assert _pull_head(b) == head_before


def test_push_by_a_replica_behind_the_remote_epoch_is_refused_and_keeps_its_ops(
    world: tuple,
) -> None:
    a, b, store = world
    _synced(a, b, store)
    r.make_task(b, "DEM-002")
    pending = _pending(b)
    _open_new_epoch(a, store)
    remote_head = store.get_ref(_uid(a), "main")

    with pytest.raises(EpochPassedError, match=r"SYNC-105: .*export-pending -p demo"):
        _push(b, store)
    assert _pending(b) == pending
    assert store.get_ref(_uid(a), "main") == remote_head
    with pytest.raises(EpochPassedError, match="старой эпохи уже не уедут"):
        _pull(b, store)


def test_updated_replica_pulls_the_new_epoch(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    opened = _open_new_epoch(a, store)
    _set_schema(b, NEW)  # B обновилась
    assert _pull(b, store).head == opened.head


def test_schema_older_than_schema_min_is_refused_with_sync_104(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    meta = store.get_json(_uid(a), META_KEY)
    assert meta is not None
    store.cas_json(_uid(a), META_KEY, meta[1], {**meta[0], "schema_min": NEW})
    r.make_task(b, "DEM-003")
    with pytest.raises(SchemaMismatchError, match="SYNC-104: remote принимает схему не старше"):
        _push(b, store)


def test_unknown_but_well_formed_epoch_is_newer_than_the_replica(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    meta = store.get_json(_uid(a), META_KEY)
    assert meta is not None
    store.cas_json(_uid(a), META_KEY, meta[1], {**meta[0], "epoch": "0099_future"})
    r.make_task(b, "DEM-004")
    with pytest.raises(EpochPassedError, match="SYNC-105"):
        _push(b, store)
    with pytest.raises(EpochPassedError, match="SYNC-105"):
        _pull(b, store)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("v", "1"),
        ("v", True),
        ("v", 2),
        ("epoch", "../evil"),
        ("epoch", 50),
        ("epoch", "x" * 200),
        ("schema_min", None),
        ("project_uid", "other"),
        ("snapshot_every", 0),
        ("snapshot_every", 10**12),
    ],
)
def test_malformed_meta_is_refused_with_sync_102(world: tuple, field: str, value: object) -> None:
    a, b, store = world
    _synced(a, b, store)
    meta = store.get_json(_uid(a), META_KEY)
    assert meta is not None
    store.cas_json(_uid(a), META_KEY, meta[1], {**meta[0], field: value})  # type: ignore[dict-item]
    r.make_task(b, "DEM-005")
    with pytest.raises(ObjectMissingError, match=r"SYNC-102: meta\.json"):
        _push(b, store)
    with pytest.raises(ObjectMissingError, match=r"SYNC-102: meta\.json"):
        _pull(b, store)


def test_status_reports_both_epochs(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    _open_new_epoch(a, store)
    st = status.status(b.sf, r.SLUG, store=store)
    assert (st.epoch_local, st.epoch_remote) == (OLD_EPOCH, NEW)


# --- export-pending ---------------------------------------------------------------


def test_export_pending_writes_the_unpushed_ops_in_changeset_form(
    world: tuple, tmp_path: Path
) -> None:
    a, b, store = world
    _synced(a, b, store)
    r.make_task(b, "DEM-006")
    _open_new_epoch(a, store)
    with transactional(b.sf) as s:
        expected = {
            row.op_id
            for row in s.scalars(select(SyncOpModel).where(SyncOpModel.changeset.is_(None)))
        }
    out = tmp_path / "pending.json"

    count = epochs.export_pending(b.sf, r.SLUG, path=out)

    assert count == len(expected) > 0
    assert out.stat().st_mode & 0o777 == 0o600
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert (doc["v"], doc["kind"], doc["slug"], doc["ops"]) == (1, "pending", r.SLUG, count)
    cs = from_dict(doc["changeset"])  # та же проверка формы, что у changeset'а remote
    assert {op.op_id for op in cs.ops} == expected
    assert (cs.epoch, cs.schema_head, cs.project) == (OLD_EPOCH, OLD, _uid(b))
    assert any(op.entity_kind == "task" for op in cs.ops)
    assert _pending(b) == count  # журнал не тронут

    with pytest.raises(FileExistsError):
        epochs.export_pending(b.sf, r.SLUG, path=out)


def test_export_pending_without_ops_is_an_empty_envelope(world: tuple) -> None:
    a, b, store = world
    _synced(a, b, store)
    data, count = epochs.render_pending(b.sf, r.SLUG)
    assert count == 0
    assert json.loads(data)["changeset"] is None


def _pull_head(rep: r.Replica) -> str | None:
    from cod_doc.services.sync_service import _local

    with transactional(rep.sf) as s:
        return _local.local_head(s, rep.project_id)


def test_pending_phrase_agrees_with_the_number() -> None:
    assert [epochs.pending_phrase(n) for n in (1, 3, 5, 11, 21, 22, 112)] == [
        "1 незапушенная операция",
        "3 незапушенные операции",
        "5 незапушенных операций",
        "11 незапушенных операций",
        "21 незапушенная операция",
        "22 незапушенные операции",
        "112 незапушенных операций",
    ]
