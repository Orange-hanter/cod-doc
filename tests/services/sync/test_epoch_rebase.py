"""CLO-050: отставшая реплика стартует со снапшота новой эпохи (RFC 29 §3.6 п. 3, Q-012).

Acceptance: «Реплика на старой эпохе после cod-doc update и pull загружает
снапшот новой эпохи (с проверкой по голове, как clone), changeset'ы старой
эпохи не применяются; changeset старой эпохи, пришедший на реплику новой
эпохи, отвергается структурной ошибкой; дайджест после этого равен голове;
инвариант capability §5 выполняется и закреплён тестом».

Смена эпохи моделируется как в ``test_epochs.py``: реестр ``sync_epochs``
подменён так, что голова цепочки (``0052``) открывает эпоху; реплика «до
обновления» — запись ``0051`` в ``alembic_version``, ``cod-doc update`` —
запись головы обратно.

Ход отставания: A и B синхронны в старой эпохе; A пушит ещё changeset
старой эпохи (X: новая задача, правка описания, снятый тег), затем
обновляется и открывает новую эпоху (S, снапшот ``snapshots/<NEW>/S``).
B до X не дошла и обновляется уже после открытия.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra import sync_epochs
from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import Hlc
from cod_doc.infra.models import (
    ActivityEventModel,
    ChangesetModel,
    DocumentModel,
    ProjectModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.infra.remote.base import META_KEY
from cod_doc.services import doc_service
from cod_doc.services.sync_service import _local, apply, digest, pull, push, snapshot
from cod_doc.services.sync_service.changeset import Changeset, Op, decode, encode, sha
from cod_doc.services.sync_service.errors import EpochPassedError, ObjectMissingError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

OLD = "0051_sync_conflict"
NEW = "0052_sync_conflict_opened_hlc"
OLD_EPOCH = "0050_sync_rank"
SECRET = "vault"


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


@pytest.fixture
def applied(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Шпион ``apply.apply_changeset``: (sha, эпоха) каждого применённого changeset'а."""
    seen: list[tuple[str, str]] = []
    original = apply.apply_changeset

    def spy(session: object, project: object, sha_: str, cs: Changeset, **kw: object) -> object:
        seen.append((sha_, cs.epoch))
        return original(session, project, sha_, cs, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(apply, "apply_changeset", spy)
    return seen


def _set_schema(rep: r.Replica, revision: str) -> None:
    with rep.engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": revision})


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote, **kw: bool) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store, **kw)


def _uid(rep: r.Replica) -> str:
    with transactional(rep.sf) as s:
        return str(s.get_one(ProjectModel, rep.project_id).uid)


def _head(rep: r.Replica) -> str | None:
    with transactional(rep.sf, commit=False) as s:
        return _local.local_head(s, rep.project_id)


def _digest_and_expected(rep: r.Replica) -> tuple[str, str]:
    with transactional(rep.sf, commit=False) as s:
        head = _local.local_head(s, rep.project_id)
        assert head is not None
        row = s.get_one(ChangesetModel, head)
        return digest.compute(s, rep.project_id), row.digest


def _secret(rep: r.Replica) -> None:
    """Restricted-документ: строка local, не уезжает и снапшотом не заменяется."""
    with transactional(rep.sf) as s:
        doc_service.create(
            s,
            project_id=rep.project_id,
            doc_key=SECRET,
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="vault",
            author=r.AUTHOR,
            path=f"docs/{SECRET}.md",
            sensitivity=Sensitivity.RESTRICTED,
            reindex=False,
        )


def _description(rep: r.Replica, task_id: str) -> str | None:
    return r.task_description(rep, task_id)


def _tasks(rep: r.Replica) -> set[str]:
    with transactional(rep.sf, commit=False) as s:
        return set(s.scalars(select(TaskModel.task_id)))


def _lagging(a: r.Replica, b: r.Replica, store: FileRemote) -> tuple[str, str]:
    """B отстала на changeset старой эпохи X и на открытие новой S; вернуть (X, S)."""
    r.make_task(a, "DEM-001")
    r.make_task(a, "DEM-002")
    r.make_doc(a, "guide", {"intro": "hello"})
    r.tag_doc(a, "guide", "t1")
    _push(a, store)
    _pull(b, store)
    _secret(b)
    assert r.pending(b) == 0

    r.make_task(a, "DEM-010")  # создание
    r.set_description(a, "DEM-001", "правка старой эпохи")  # правка
    r.untag_doc(a, "guide")  # удаление (тег-таблица без uid)
    old = _push(a, store)
    assert old.head is not None

    _set_schema(a, NEW)  # A обновилась
    r.make_task(a, "DEM-100")
    opened = _push(a, store)
    assert opened.epoch_opened == NEW
    assert opened.head is not None
    return old.head, opened.head


# --- acceptance ---------------------------------------------------------------------


def test_lagging_replica_after_update_starts_from_the_epoch_snapshot(
    world: tuple, applied: list[tuple[str, str]]
) -> None:
    a, b, store = world
    old, opened = _lagging(a, b, store)
    applied.clear()  # pull B при синхронизации в старой эпохе — подготовка
    _set_schema(b, NEW)  # cod-doc update на B

    result = _pull(b, store)

    assert result.rebased == opened
    assert result.head == opened == _head(b)
    # Ни changeset старой эпохи, ни голова снапшота не проигрывались: строки — со снапшота.
    assert applied == []
    assert result.applied == []
    local, expected = _digest_and_expected(b)
    assert local == expected
    assert r.authored_state(b) == r.authored_state(a)
    assert _tasks(b) >= {"DEM-001", "DEM-002", "DEM-010", "DEM-100"}
    assert _description(b, "DEM-001") == "правка старой эпохи"
    # Журнал и индекс — из объектов remote по адресам, как у clone.
    with transactional(b.sf, commit=False) as s:
        assert _local.is_applied(s, old)
        assert _local.is_applied(s, opened)
        marks = set(s.scalars(select(SyncOpModel.changeset).where(SyncOpModel.changeset == old)))
        assert marks == {old}
        # Local-строки и local-колонки реплики не тронуты.
        project = s.get_one(ProjectModel, b.project_id)
        assert project.root_path == "/tmp/b"
        assert s.scalars(select(DocumentModel).where(DocumentModel.doc_key == SECRET)).one()
        events = [
            dict(e.payload)
            for e in s.scalars(
                select(ActivityEventModel).where(ActivityEventModel.kind == "sync.epoch_rebased")
            )
        ]
    assert len(events) == 1
    assert events[0]["epoch"] == NEW
    assert events[0]["snapshot"] == opened
    assert r.pending(b) == 0


def test_after_the_rebase_the_tail_and_pushes_flow_both_ways(
    world: tuple, applied: list[tuple[str, str]]
) -> None:
    a, b, store = world
    _lagging(a, b, store)
    r.make_task(a, "DEM-101")  # хвост новой эпохи после снапшота
    tail = _push(a, store)
    applied.clear()
    _set_schema(b, NEW)

    result = _pull(b, store)

    assert result.applied == [tail.head]
    assert [epoch for _, epoch in applied] == [NEW]
    r.set_description(b, "DEM-101", "с B после перехода")
    _push(b, store)
    _pull(a, store)
    assert _description(a, "DEM-101") == "с B после перехода"
    assert r.authored_state(a) == r.authored_state(b)
    for rep in (a, b):
        local, expected = _digest_and_expected(rep)
        assert local == expected


def _forged_old_epoch(rep: r.Replica, store: FileRemote) -> str:
    """Changeset старой эпохи поверх головы remote новой эпохи (старый клиент)."""
    uid = _uid(rep)
    parent = store.get_ref(uid, "main")
    assert parent is not None
    with transactional(rep.sf, commit=False) as s:
        task_uid = str(s.scalars(select(TaskModel.uid).where(TaskModel.task_id == "DEM-002")).one())
    hlc = Hlc.parse(decode(store.get_object(uid, parent.sha), parent.sha).hlc_max)
    stamp = str(Hlc(hlc.ms + 1, 0, hlc.replica_uid))
    cs = Changeset(
        project=uid,
        parents=[parent.sha],
        replica=hlc.replica_uid,
        actor=r.AUTHOR,
        epoch=OLD_EPOCH,
        schema_head=OLD,
        hlc_max=stamp,
        digest="0" * 64,
        ops=[
            Op(
                op_id="01K7Z0000000000000000000YY",
                hlc=stamp,
                entity_kind="task",
                entity_uid=task_uid,
                kind="upsert",
                fields={"description": "из прошлой эпохи"},
                base={"description": "old"},
                author=r.AUTHOR,
            )
        ],
    )
    address = sha(cs)
    store.put_object(uid, address, encode(cs))
    assert store.cas_ref(uid, "main", parent, address) is not None
    return address


def test_invariant_changeset_of_a_past_epoch_is_never_applied_to_a_new_epoch_replica(
    world: tuple, applied: list[tuple[str, str]]
) -> None:
    """Capability remote-sync §5: «Changeset прошлой эпохи не применяется к реплике новой эпохи».

    Оба пути: changeset'ы старой эпохи между головой отставшей реплики и
    открытием эпохи (вместо них — снапшот) и changeset старой эпохи поверх
    истории новой (структурный отказ, ничего не применено).
    """
    a, b, store = world
    old, _opened = _lagging(a, b, store)
    applied.clear()
    _set_schema(b, NEW)
    _pull(b, store)
    assert (old, OLD_EPOCH) not in applied

    _forged_old_epoch(a, store)
    head = _head(b)
    state = r.authored_state(b)
    with pytest.raises(EpochPassedError, match=r"^SYNC-105: .*эпохи 0050_sync_rank.*0052"):
        _pull(b, store)
    assert _head(b) == head
    assert r.authored_state(b) == state
    assert _description(b, "DEM-002") == "old"
    assert all(epoch == NEW for _, epoch in applied)
    local, expected = _digest_and_expected(b)
    assert local == expected


# --- отказы -------------------------------------------------------------------------


def test_unpushed_ops_on_top_of_the_old_epoch_refuse_the_rebase(world: tuple) -> None:
    a, b, store = world
    _lagging(a, b, store)
    _set_schema(b, NEW)
    r.make_task(b, "DEM-200")  # правка поверх состояния старой эпохи
    pending = r.pending(b)
    head = _head(b)

    with pytest.raises(EpochPassedError, match=r"SYNC-105: .*export-pending -p demo.*clone"):
        _pull(b, store)
    assert r.pending(b) == pending
    assert _head(b) == head
    assert "DEM-010" not in _tasks(b)


def test_dry_run_names_the_snapshot_and_writes_nothing(world: tuple) -> None:
    a, b, store = world
    _, opened = _lagging(a, b, store)
    _set_schema(b, NEW)
    head = _head(b)

    result = _pull(b, store, dry_run=True)

    assert result.rebased == opened
    assert _head(b) == head
    assert "DEM-010" not in _tasks(b)


def _tamper_snapshot(store: FileRemote, a: r.Replica) -> None:
    found = snapshot.pointer(store, _uid(a))
    assert found is not None
    data = store.get_snapshot(_uid(a), found[0])
    lines = list(snapshot._lines(data))
    for line in lines:
        if isinstance(line, list) and line[:2] == ["row", "task"]:
            line[3]["title"] = "подмена"  # type: ignore[index]
    writer = snapshot._Writer(10**10)
    for line in lines:
        writer.line(line)
    (store.root / _uid(a) / found[0].key).write_bytes(writer.finish())


def test_tampered_epoch_snapshot_fails_the_digest_and_changes_nothing(world: tuple) -> None:
    a, b, store = world
    _lagging(a, b, store)
    _set_schema(b, NEW)
    _tamper_snapshot(store, a)
    head = _head(b)
    state = r.authored_state(b)

    with pytest.raises(ObjectMissingError, match=r"SYNC-102.*дайджест"):
        _pull(b, store)
    assert _head(b) == head
    assert r.authored_state(b) == state
    with transactional(b.sf, commit=False) as s:
        assert s.scalars(select(DocumentModel).where(DocumentModel.doc_key == SECRET)).one()


def test_snapshot_pointer_of_another_epoch_is_refused_with_sync_102(world: tuple) -> None:
    a, b, store = world
    _lagging(a, b, store)
    _set_schema(b, NEW)
    meta = store.get_json(_uid(a), META_KEY)
    assert meta is not None
    pointer = dict(meta[0]["snapshot"])  # type: ignore[arg-type]
    store.cas_json(
        _uid(a), META_KEY, meta[1], {**meta[0], "snapshot": {**pointer, "epoch": OLD_EPOCH}}
    )
    head = _head(b)
    with pytest.raises(ObjectMissingError, match=r"SYNC-102: .*снапшот.*эпох"):
        _pull(b, store)
    assert _head(b) == head
