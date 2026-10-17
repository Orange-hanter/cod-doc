"""CLO-013: fetch и pull с fast-forward и применением чужих операций (RFC 29 §3.6, §10.6).

Acceptance: «Применение под sync_capture.suspended, трансляция uid → row_id,
одна транзакция на changeset, fast-forward без merge-changeset; changeset со
схемой новее своей → SYNC-104 без частичного применения; пересборка derived
(link_sync, FTS) по затронутым сущностям; тест на SCN-085».

Реплики — две SQLite в tmp, remote — ``FileRemote`` в tmp.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text

from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import Hlc
from cod_doc.infra.models import (
    ActivityEventModel,
    ChangesetModel,
    LinkModel,
    ProjectModel,
    SectionModel,
    SyncOpModel,
    SyncRefModel,
    TaskModel,
)
from cod_doc.infra.remote.file import FileRemote
from cod_doc.services import doc_service
from cod_doc.services.sync_service import _local, pull, push
from cod_doc.services.sync_service.changeset import Changeset, Op, decode, encode, sha
from cod_doc.services.sync_service.errors import MergeRequiredError, SchemaMismatchError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.base import RefValue


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote, **kw: object) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store, **kw)  # type: ignore[arg-type]


def _pull(rep: r.Replica, store: FileRemote, **kw: object) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store, **kw)  # type: ignore[arg-type]


def _project_uid(rep: r.Replica) -> str:
    with transactional(rep.sf) as s:
        return str(s.get_one(ProjectModel, rep.project_id).uid)


def _section_positions(rep: r.Replica, doc_key: str) -> list[tuple[str, int]]:
    doc = r.doc_id(rep, doc_key)
    with transactional(rep.sf) as s:
        rows = s.execute(
            select(SectionModel.anchor, SectionModel.position)
            .where(SectionModel.document_id == doc)
            .order_by(SectionModel.position)
        )
        return [(a, p) for a, p in rows]


# --- fast-forward -----------------------------------------------------------------


def test_pull_reproduces_authored_state_of_the_other_replica(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001", description="первая")
    r.make_task(a, "DEM-002", blocked_by=["DEM-001"])  # ребро dependency: FK — uid задач
    r.make_doc(a, "other", {"intro": "hello"})
    r.make_doc(a, "spec", {"intro": "see [[doc:other]]", "body": "second", "tail": "third"})
    r.tag_doc(a, "spec", "sync")  # тег-таблица: составной PK, строка ищется по ключу ребра
    pushed = _push(a, store)

    result = _pull(b, store)

    assert result.applied == pushed.changesets
    assert result.ops == pushed.ops
    assert result.fast_forward is True
    assert result.head == pushed.head
    state = r.authored_state(b)
    assert state == r.authored_state(a)
    assert {"task", "dependency", "section", "document_tag", "plan_section"} <= state.keys()
    # position — local, но пересчитан из ранга: тот же порядок и те же индексы.
    assert _section_positions(b, "spec") == _section_positions(a, "spec")


def test_apply_writes_no_echo_ops_and_repeated_pull_is_noop(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    pushed = _push(a, store)
    _pull(b, store)

    assert r.pending(b) == 0  # suspended: применение не оставило хвоста
    state = r.authored_state(b)
    again = _pull(b, store)

    assert again.applied == []
    assert again.ops == 0
    assert again.head == pushed.head
    assert r.authored_state(b) == state
    with transactional(b.sf) as s:
        foreign = s.scalars(select(SyncOpModel.changeset).distinct()).all()
        cs = s.get_one(ChangesetModel, pushed.head)
        head = s.get_one(SyncRefModel, (b.project_id, "@local", "main"))
        seen = s.get_one(SyncRefModel, (b.project_id, "origin", "main"))
    # Чужие операции — история с changeset, не хвост.
    assert foreign == [pushed.head]
    assert cs.applied_at is not None
    assert head.sha == pushed.head
    assert seen.sha == pushed.head


def test_updates_and_deletes_follow_on_the_next_pull(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    r.make_doc(a, "spec", {"intro": "one", "body": "two", "tail": "three"})
    r.tag_doc(a, "spec", "sync")
    _push(a, store)
    _pull(b, store)

    r.set_description(a, "DEM-001", "новое описание")
    r.untag_doc(a, "spec")
    with transactional(a.sf) as s:
        doc_service.delete_section(
            s,
            project_id=a.project_id,
            document_id=r.doc_id(a, "spec"),
            anchor="body",
            author=r.AUTHOR,
            reindex=False,
        )
    _push(a, store)
    result = _pull(b, store)

    assert len(result.applied) == 1
    assert r.task_description(b, "DEM-001") == "новое описание"
    assert _section_positions(b, "spec") == [("intro", 0), ("tail", 1)]
    state = r.authored_state(b)
    assert state == r.authored_state(a)
    assert "document_tag" not in state


def test_pull_rebuilds_links_and_search_index_of_touched_entities(pair: tuple) -> None:
    a, b, store = pair
    r.make_doc(a, "other", {"intro": "target"})
    r.make_doc(a, "spec", {"intro": "see [[doc:other]] and the zebra"})
    _push(a, store)

    _pull(b, store)

    spec = r.doc_id(b, "spec")
    with transactional(b.sf) as s:
        section = s.scalars(select(SectionModel).where(SectionModel.document_id == spec)).one()
        links = s.scalars(select(LinkModel).where(LinkModel.from_section_id == section.row_id))
        resolved = [(link.to_doc_key, link.resolved) for link in links]
        hits = s.execute(
            text(
                "SELECT ref FROM db_search_idx WHERE db_search_idx MATCH 'zebra' "
                "AND project_id = :p"
            ),
            {"p": b.project_id},
        ).scalars()
        found = list(hits)
    assert resolved == [("other", True)]
    assert found == ["spec"]


def test_local_tail_on_other_entities_survives_pull_and_ships_later(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    _push(a, store)
    _pull(b, store)
    r.make_task(a, "DEM-002")
    _push(a, store)
    r.set_description(b, "DEM-001", "правка B")
    tail = r.pending(b)

    result = _pull(b, store)

    assert len(result.applied) == 1
    assert r.pending(b) == tail  # хвост B не тронут и не упакован
    assert r.task_description(b, "DEM-002") == "old"
    assert r.task_description(b, "DEM-001") == "правка B"


def test_tail_touching_the_same_entity_is_left_to_section_c(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    _push(a, store)
    _pull(b, store)
    r.set_description(a, "DEM-001", "A")
    _push(a, store)
    r.set_description(b, "DEM-001", "B")
    before = r.authored_state(b)

    with pytest.raises(MergeRequiredError, match="секция C"):
        _pull(b, store)

    assert r.authored_state(b) == before
    assert r.task_description(b, "DEM-001") == "B"


def test_dry_run_reports_the_would_be_head_and_writes_nothing(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    first = _push(a, store)
    r.make_task(a, "DEM-002")
    second = _push(a, store)
    before = r.authored_state(b)

    result = _pull(b, store, dry_run=True)

    assert result.applied == [first.head, second.head]
    assert result.head == second.head
    assert result.ops == first.ops + second.ops
    assert r.authored_state(b) == before


def test_fetch_records_remote_head_without_applying(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    pushed = _push(a, store)

    new = pull.fetch(b.sf, r.SLUG, store=store)

    assert new == pushed.changesets
    assert r.task_description(b, "DEM-001") is None
    with transactional(b.sf) as s:
        seen = s.get_one(SyncRefModel, (b.project_id, "origin", "main"))
        head = s.get(SyncRefModel, (b.project_id, "@local", "main"))
    assert seen.sha == pushed.head
    assert head is None


def test_pull_emits_one_sync_pulled_event(pair: tuple) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    pushed = _push(a, store)

    _pull(b, store)
    _pull(b, store)  # пустой pull события не пишет

    with transactional(b.sf) as s:
        events = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "sync.pulled")
        ).all()
    assert len(events) == 1
    assert events[0].actor_id == r.AUTHOR
    assert events[0].scope_kind == "project"
    assert events[0].payload == {
        "remote": "origin",
        "applied": pushed.changesets,
        "ops": pushed.ops,
        "conflicts": [],
        "fast_forward": True,
    }


def test_own_changeset_after_lost_commit_is_settled_not_reapplied(pair: tuple) -> None:
    """Push упал между CAS и фиксацией: remote на нашем changeset'е, хвост не помечен."""
    a, _b, store = pair
    r.make_task(a, "DEM-001")
    pushed = _push(a, store)
    with transactional(a.sf) as s:  # откатить фиксацию push'а
        s.execute(text("UPDATE sync_op SET changeset = NULL"))
        s.execute(text("DELETE FROM changeset"))
        s.execute(text("DELETE FROM sync_ref"))
    tail = r.pending(a)

    result = _pull(a, store)

    assert result.applied == []
    assert result.head == pushed.head
    assert r.pending(a) == 0
    assert tail > 0
    assert _push(a, store).changesets == []


# --- SCN-085 ----------------------------------------------------------------------


def _forged_newer_schema(a: r.Replica, store: FileRemote, parent: str) -> str:
    """Changeset поверх ``parent`` с ``schema_head`` новее головы реплик."""
    uid = _project_uid(a)
    with transactional(a.sf) as s:
        task = s.scalars(select(TaskModel)).one()
        task_uid = str(task.uid)
    parent_cs = decode(store.get_object(uid, parent), parent)
    hlc = Hlc.parse(parent_cs.hlc_max)
    stamp = str(Hlc(hlc.ms + 1, 0, hlc.replica_uid))
    cs = Changeset(
        project=uid,
        parents=[parent],
        replica=hlc.replica_uid,
        actor=r.AUTHOR,
        epoch="0050_sync_rank",
        schema_head="0099_from_the_future",
        hlc_max=stamp,
        digest="0" * 64,
        ops=[
            Op(
                op_id="01K7Z0000000000000000000ZZ",
                hlc=stamp,
                entity_kind="task",
                entity_uid=task_uid,
                kind="upsert",
                fields={"description": "с новой схемы"},
                base={"description": "old"},
                author=r.AUTHOR,
            )
        ],
    )
    address = sha(cs)
    store.put_object(uid, address, encode(cs))
    current = store.get_ref(uid, "main")
    assert store.cas_ref(uid, "main", current, address) is not None
    return address


def test_scn_085_changeset_with_newer_schema_is_refused_without_partial_apply(
    pair: tuple,
) -> None:
    a, b, store = pair
    r.make_task(a, "DEM-001")
    older = _push(a, store)
    assert older.head is not None
    _forged_newer_schema(a, store, older.head)
    before = r.authored_state(b)

    with pytest.raises(SchemaMismatchError) as err:
        _pull(b, store)

    assert err.value.code == "SYNC-104"
    with transactional(b.sf) as s:
        local = _local.schema_head(s)  # голова миграций БД, а не литерал: растёт с каждой
    assert f"remote на схеме 0099_from_the_future, у вас {local} — cod-doc update" in str(err.value)
    # Ни одна операция не применена — даже changeset'а со старой схемой под ним.
    assert r.authored_state(b) == before
    assert r.task_description(b, "DEM-001") is None
    with transactional(b.sf) as s:
        assert s.scalars(select(ChangesetModel)).all() == []
        assert s.get(SyncRefModel, (b.project_id, "@local", "main")) is None


# --- конкурентные pull ------------------------------------------------------------


class _Rendezvous(FileRemote):
    """Первые два ``get_ref`` ждут друг друга: оба pull скачивают цепочку до записи."""

    def __init__(self, inner: FileRemote) -> None:
        super().__init__(inner.root)
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.calls = 0

    def get_ref(self, project_uid: str, name: str) -> RefValue | None:
        with self.lock:
            self.calls += 1
            wait = self.calls <= 2
        if wait:
            self.barrier.wait(timeout=10)
        return super().get_ref(project_uid, name)


def test_concurrent_pulls_apply_each_changeset_once(
    pair: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два pull одной реплики: оба качают цепочку, оба пишут ``sync_ref``.

    Барьеры ставят обе записи ``sync_ref`` в гонку детерминированно: строки
    головы ``@local`` и ref'а remote на B ещё нет, и оба pull доходят до
    записи, не увидев чужой. Проигравший не получает ``IntegrityError``.
    """
    a, b, store = pair
    r.make_task(a, "DEM-001")
    first = _push(a, store)
    r.make_task(a, "DEM-002")
    second = _push(a, store)
    gate = _Rendezvous(store)
    for name in ("ensure_local_head", "set_remote_ref"):
        original = getattr(_local, name)
        barrier = threading.Barrier(2)

        def raced(*args: object, _orig: object = original, _b: threading.Barrier = barrier) -> None:
            _b.wait(timeout=10)
            _orig(*args)  # type: ignore[operator]

        monkeypatch.setattr(_local, name, raced)
    results: list[pull.PullResult] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(pull.pull(b.sf, r.SLUG, author=r.AUTHOR, store=gate))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    applied = [sha for res in results for sha in res.applied]
    assert sorted(applied) == sorted([first.head, second.head])
    assert r.authored_state(b) == r.authored_state(a)
    with transactional(b.sf) as s:
        head = s.get_one(SyncRefModel, (b.project_id, "@local", "main"))
        events = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "sync.pulled")
        ).all()
    assert head.sha == second.head
    assert sum(len(e.payload["applied"]) for e in events) == 2
