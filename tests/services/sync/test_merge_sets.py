"""CLO-021: множества add-wins и uid рёбер (RFC 29 §3.3, §3.7).

Acceptance: «Рёбра из RFC 29 §3.3 получают uid = uuid5(концы, relation); одно
ребро с двух реплик — одна строка; добавление против удаления — ребро
остаётся; тег-таблицы с составным PK работают без колонки uid.»

Каждый сценарий сводится в двух порядках push/pull (``_two_orders``): у всех
четырёх реплик один дайджест, конфликтов нет.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    DependencyModel,
    DocumentTagModel,
    SyncOpModel,
    TagModel,
    TaskModel,
)
from cod_doc.services import task_service
from cod_doc.services.sync_service import merge
from tests.services.sync import _replicas as r
from tests.services.sync import _two_orders as t

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

TASK, BLOCKER = "DEM-001", "DEM-002"
DOC, TAG = "guide/sets", "важное"


# --- uid рёбер -----------------------------------------------------------------------


def test_every_rfc_edge_table_has_a_deterministic_uid() -> None:
    edges = {
        "dependency",
        "adr_supersedes",
        "adr_relation",
        "adr_task",
        "module_dependency",
        "scenario_link",
        "story_link",
        "open_question_link",
        "commit_link",
        "affected_file",
        "document_tag",
        "task_tag",
        "story_tag",
    }
    assert {t_ for t_ in edges if reg.spec_for(t_).deterministic_uid is None} == set()
    assert {t_ for t_ in edges if merge._info(t_) is None} == set()
    # Тег-таблицы — составной PK без колонки uid: uid только вычисляется.
    for table in ("document_tag", "task_tag", "story_tag"):
        info = merge._info(table)
        assert info is not None
        assert not info.has_uid


def test_edge_uid_is_uuid5_of_the_ends_and_relation() -> None:
    ends = {"from_task_id": "t-1", "to_task_id": "t-2", "kind": "blocks"}
    uid = reg.edge_uid("dependency", ends)
    assert uid == reg.edge_uid("dependency", dict(reversed(ends.items())))
    assert uid != reg.edge_uid("dependency", {**ends, "kind": "relates"})
    assert uid != reg.edge_uid("dependency", {**ends, "from_task_id": "t-2", "to_task_id": "t-1"})
    import uuid

    assert uuid.UUID(uid).version == 5


# --- помощники ------------------------------------------------------------------------


def _tasks(a: r.Replica, _b: r.Replica, _store: FileRemote) -> None:
    r.make_task(a, TASK)
    r.make_task(a, BLOCKER)


def _add_dep(rep: r.Replica, note: str = "ждёт") -> None:
    t.tick()
    with transactional(rep.sf) as s:
        task_service.add_dependency(
            s,
            project_id=rep.project_id,
            task_id=TASK,
            blocker_task_id=BLOCKER,
            note=note,
            author=r.AUTHOR,
        )


def _remove_dep(rep: r.Replica) -> None:
    t.tick()
    with transactional(rep.sf) as s:
        task_service.remove_dependency(
            s, project_id=rep.project_id, task_id=TASK, blocker_task_id=BLOCKER, author=r.AUTHOR
        )


def _set_note(rep: r.Replica, note: str) -> None:
    t.tick()
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        s.scalars(select(DependencyModel)).one().note = note


def _deps(rep: r.Replica) -> list[tuple[str, str, str, str | None]]:
    with transactional(rep.sf, commit=False) as s:
        tasks = {row.row_id: row.task_id for row in s.scalars(select(TaskModel))}
        return [
            (tasks[d.from_task_id], tasks[d.to_task_id], d.kind, d.note)
            for d in s.scalars(select(DependencyModel))
        ]


def _doc_with_tag(a: r.Replica, _b: r.Replica, _store: FileRemote) -> None:
    r.make_doc(a, DOC, {"intro": "текст"})
    with transactional(a.sf) as s:
        cap.set_author(s, r.AUTHOR)
        s.add(TagModel(project_id=a.project_id, name=TAG))


def _link_tag(rep: r.Replica) -> None:
    """Связь документа с уже общим тегом — ORM с захватом (сервиса тегов нет, F3)."""
    t.tick()
    with transactional(rep.sf) as s:
        cap.set_author(s, r.AUTHOR)
        tag = s.scalars(select(TagModel).where(TagModel.name == TAG)).one()
        s.add(DocumentTagModel(document_id=r.doc_id(rep, DOC), tag_id=tag.row_id))


def _unlink_tag(rep: r.Replica) -> None:
    t.tick()
    r.untag_doc(rep, DOC)


def _doc_tags(rep: r.Replica) -> int:
    with transactional(rep.sf, commit=False) as s:
        return int(s.scalar(select(func.count()).select_from(DocumentTagModel)) or 0)


def _edge_ops(rep: r.Replica, table: str) -> set[str]:
    with transactional(rep.sf, commit=False) as s:
        return set(
            s.scalars(select(SyncOpModel.entity_uid).where(SyncOpModel.entity_kind == table))
        )


def _converge(universes: tuple[t.Universe, t.Universe]) -> None:
    for u in universes:
        assert t.exchange(u).conflicts == []
        t.settle(u)
        assert t.signatures(u.a) == t.signatures(u.b) == set()
        t.assert_converged(u)
    assert len({t.digest_of(rep) for u in universes for rep in u.replicas}) == 1


# --- одно ребро с двух реплик -----------------------------------------------------------


@pytest.mark.parametrize("notes", [("ждёт", "ждёт"), ("от A", "от B")])
def test_same_dependency_added_on_both_replicas_is_one_row(
    tmp_path: Path, notes: tuple[str, str]
) -> None:
    """Одно ребро с двух реплик — один uid и одна строка; разная note — LWW по полю."""

    def edits(a: r.Replica, b: r.Replica) -> None:
        _add_dep(a, notes[0])
        _add_dep(b, notes[1])
        assert _edge_ops(a, "dependency") == _edge_ops(b, "dependency")  # один uid

    universes = t.two_orders(tmp_path, _tasks, edits)
    _converge(universes)
    for u in universes:
        for rep in u.replicas:
            assert _deps(rep) == [(TASK, BLOCKER, "blocks", notes[1])]  # B писала позже
    t.dispose(*universes)


def test_same_tag_link_added_on_both_replicas_is_one_row(tmp_path: Path) -> None:
    """Тег-таблица с составным PK: uid вычисляется, строка находится по концам."""

    def edits(a: r.Replica, b: r.Replica) -> None:
        _link_tag(a)
        _link_tag(b)
        uids = _edge_ops(a, "document_tag")
        assert uids == _edge_ops(b, "document_tag")
        assert len(uids) == 1

    universes = t.two_orders(tmp_path, _doc_with_tag, edits)
    _converge(universes)
    for u in universes:
        assert _doc_tags(u.a) == _doc_tags(u.b) == 1
    t.dispose(*universes)


# --- добавление против удаления ---------------------------------------------------------


def _tasks_with_dep(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    _tasks(a, b, store)
    _add_dep(a, "общая")


@pytest.mark.parametrize("deleter", ["a", "b"])
def test_edge_edit_against_its_delete_keeps_the_edge(tmp_path: Path, deleter: str) -> None:
    """Одна реплика удаляет ребро, другая правит его note: ребро остаётся с правкой."""

    def edits(a: r.Replica, b: r.Replica) -> None:
        gone, kept = (a, b) if deleter == "a" else (b, a)
        _remove_dep(gone)
        _set_note(kept, "правка")

    universes = t.two_orders(tmp_path, _tasks_with_dep, edits)
    _converge(universes)
    for u in universes:
        for rep in u.replicas:
            assert _deps(rep) == [(TASK, BLOCKER, "blocks", "правка")]
    t.dispose(*universes)


@pytest.mark.parametrize("deleter", ["a", "b"])
def test_tag_readded_against_its_delete_stays(tmp_path: Path, deleter: str) -> None:
    """Тег снят на одной реплике, на другой снят и поставлен заново: тег остаётся."""

    def setup(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
        _doc_with_tag(a, b, store)
        _link_tag(a)

    def edits(a: r.Replica, b: r.Replica) -> None:
        gone, kept = (a, b) if deleter == "a" else (b, a)
        _unlink_tag(gone)
        _unlink_tag(kept)
        _link_tag(kept)

    universes = t.two_orders(tmp_path, setup, edits)
    _converge(universes)
    for u in universes:
        assert _doc_tags(u.a) == _doc_tags(u.b) == 1
    t.dispose(*universes)


def test_edge_deleted_on_both_replicas_is_gone(tmp_path: Path) -> None:
    def edits(a: r.Replica, b: r.Replica) -> None:
        _remove_dep(a)
        _remove_dep(b)

    universes = t.two_orders(tmp_path, _tasks_with_dep, edits)
    _converge(universes)
    for u in universes:
        assert _deps(u.a) == _deps(u.b) == []
    t.dispose(*universes)
