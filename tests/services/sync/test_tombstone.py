"""CLO-022: tombstone родителя и конфликт «правка под мёртвым родителем» (RFC 29 §3.7, Q-008).

Acceptance: «Удаление родителя — одна операция; на другой реплике поддерево
сносит каскад; операция над потомком мёртвого родителя → конфликт
``delete_edit`` с JSON-снимком поддерева; resolve theirs восстанавливает
поддерево с правкой; тест на SCN-092.»

Обе очерёдности синхронизации: «удаление первым» — SCN-092 как написан
(A удаляет и пушит, B с правкой сливает), «правка первой» — B пушит правку,
сливает A с удалением в хвосте. Конфликт — local (§3.2): он открывается на
той реплике, которая сливала, и только там есть данные для снимка. В конце
каждого теста конфликт разрешён, открытых нет ни на одной реплике, дайджесты
совпадают.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.infra import sync_capture as cap
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DocumentModel,
    RevisionModel,
    SectionModel,
    SyncOpModel,
    TaskModel,
)
from cod_doc.services import doc_service
from cod_doc.services.sync_service import conflicts, digest, pull, push
from cod_doc.services.sync_service.changeset import decode
from cod_doc.services.sync_service.errors import ConflictResolveError
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

DOC = "cod-doc-task-plan"
SECTIONS = {f"s{i:02d}": f"тело {i}" for i in range(12)}
EDITED = "s05"
_TICK = 0.003


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _digest(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return digest.compute(s, rep.project_id)


def _open(rep: r.Replica) -> list[dict[str, object]]:
    with transactional(rep.sf, commit=False) as s:
        return conflicts.list_conflicts(s, rep.project_id)


def _sections(rep: r.Replica) -> dict[str, str]:
    with transactional(rep.sf, commit=False) as s:
        rows = s.execute(
            select(SectionModel.anchor, SectionModel.body)
            .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
            .where(DocumentModel.project_id == rep.project_id, DocumentModel.doc_key == DOC)
        )
        return {str(k): str(v) for k, v in rows.all()}


def _has_doc(rep: r.Replica) -> bool:
    with transactional(rep.sf, commit=False) as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(DocumentModel)
                .where(DocumentModel.project_id == rep.project_id, DocumentModel.doc_key == DOC)
            )
            or 0
        ) > 0


def _delete_doc(rep: r.Replica) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        doc_service.delete(s, project_id=rep.project_id, doc_key=DOC, author=r.AUTHOR)


def _edit_section(rep: r.Replica, body: str) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        doc_service.patch_section(
            s,
            project_id=rep.project_id,
            document_id=r.doc_id(rep, DOC),
            anchor=EDITED,
            new_body=body,
            author=r.AUTHOR,
            reindex=False,
        )


def _common_doc(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    r.make_doc(a, DOC, SECTIONS)
    _push(a, store)
    _pull(b, store)
    assert _sections(b) == SECTIONS


def _ops_of_head(rep: r.Replica, store: FileRemote, head: str | None) -> list[tuple[str, str]]:
    assert head is not None
    with transactional(rep.sf, commit=False) as s:
        from cod_doc.infra.models import ProjectModel

        uid = s.get_one(ProjectModel, rep.project_id).uid
    cs = decode(store.get_object(str(uid), head), head)
    return [(op.entity_kind, op.kind) for op in cs.ops]


def _converged(a: r.Replica, b: r.Replica, store: FileRemote) -> None:
    assert r.pending(a) == r.pending(b) == 0
    assert r.authored_state(a) == r.authored_state(b)
    assert _digest(a) == _digest(b)
    assert [c["uid"] for c in _open(a)] == [c["uid"] for c in _open(b)]
    for rep in (a, b):
        assert digest.verify(rep.sf, r.SLUG, store=store).equal


def _snapshot_rows(conflict: dict[str, object]) -> list[dict[str, object]]:
    theirs = conflict["theirs"]
    assert isinstance(theirs, dict)
    rows = theirs["rows"]
    assert isinstance(rows, list)
    return rows


def _resolve(rep: r.Replica, uid: str, take: str) -> dict[str, object]:
    with transactional(rep.sf) as s:
        return conflicts.resolve_conflict(s, rep.project_id, uid, take=take, author=r.AUTHOR)


def _assert_snapshot(conflict: dict[str, object], edited: str) -> None:
    assert conflict["kind"] == "delete_edit"
    assert conflict["entity_kind"] == "document"
    assert conflict["field"] is None
    rows = _snapshot_rows(conflict)
    tables = [row["table"] for row in rows]
    assert tables[0] == "document"  # родитель раньше детей: восстановление по порядку
    assert tables.count("section") == len(SECTIONS)
    bodies = {
        row["fields"]["anchor"]: row["fields"]["body"]  # type: ignore[index]
        for row in rows
        if row["table"] == "section"
    }
    assert bodies[EDITED] == edited
    # Снимок — только уезжающие колонки: ни row_id, ни local-колонок.
    for row in rows:
        fields = row["fields"]
        assert isinstance(fields, dict)
        assert "row_id" not in fields
        assert "position" not in fields
        assert "projection_hash" not in fields


# --- SCN-092: удаление первым ---------------------------------------------------


def test_scn_092_delete_wins_and_resolve_theirs_restores_the_subtree(pair: tuple) -> None:
    """SCN-092: A удаляет документ, B правит секцию; A push, B pull, B resolve theirs."""
    a, b, store = pair
    _common_doc(a, b, store)
    _delete_doc(a)  # шаг 1
    _edit_section(b, "правка B")  # шаг 2

    pushed = _push(a, store)  # шаг 3
    assert _ops_of_head(a, store, pushed.head) == [("document", "delete")]  # одна операция
    pulled = _pull(b, store)

    assert not _has_doc(b)  # документ и 12 секций сняты каскадом
    with transactional(b.sf, commit=False) as s:
        assert s.scalar(select(func.count()).select_from(SectionModel)) == 0
    (conflict,) = _open(b)
    assert pulled.conflicts == [conflict["uid"]]
    _assert_snapshot(conflict, "правка B")
    # Правка под мёртвым родителем из хвоста ушла: её операция не уедет.
    assert r.pending(b) == 0
    assert _open(a) == []
    _push(b, store)
    _pull(a, store)
    assert _digest(a) == _digest(b)

    resolved = _resolve(b, str(conflict["uid"]), "theirs")  # шаг 4

    assert resolved["status"] == "resolved"
    assert _sections(b) == {**SECTIONS, EDITED: "правка B"}
    with transactional(b.sf, commit=False) as s:
        doc = s.scalars(select(DocumentModel).where(DocumentModel.doc_key == DOC)).one()
        assert doc.uid == conflict["entity_uid"]  # тот же uid, а не новая строка
        rev = s.scalars(select(RevisionModel).where(RevisionModel.entity_id == doc.row_id)).all()
        assert any("sync.resolve" in (row.diff or "") for row in rev)
        events = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "sync.conflict_resolved")
        ).all()
        assert [e.payload["take"] for e in events] == ["theirs"]
    _push(b, store)
    _pull(a, store)
    assert _sections(a) == {**SECTIONS, EDITED: "правка B"}
    _converged(a, b, store)


def test_delete_first_resolve_ours_keeps_the_delete(pair: tuple) -> None:
    a, b, store = pair
    _common_doc(a, b, store)
    _delete_doc(a)
    _edit_section(b, "правка B")
    _push(a, store)
    (uid,) = _pull(b, store).conflicts

    _resolve(b, uid, "ours")

    assert not _has_doc(b)
    _push(b, store)
    _pull(a, store)
    assert not _has_doc(a)
    _converged(a, b, store)


def test_new_child_under_a_deleted_parent_goes_into_the_snapshot(pair: tuple) -> None:
    """B добавил секцию в документ, который A удалил: она в снимке и не уезжает."""
    a, b, store = pair
    _common_doc(a, b, store)
    _delete_doc(a)
    time.sleep(_TICK)
    with transactional(b.sf) as s:
        doc_service.add_section(
            s,
            project_id=b.project_id,
            document_id=r.doc_id(b, DOC),
            anchor="new",
            heading="New",
            level=2,
            position=len(SECTIONS),
            body="новая секция B",
            author=r.AUTHOR,
            reindex=False,
        )
    _push(a, store)
    (uid,) = _pull(b, store).conflicts
    (conflict,) = _open(b)
    anchors = [
        row["fields"]["anchor"] for row in _snapshot_rows(conflict) if row["table"] == "section"
    ]  # type: ignore[index]
    assert "new" in anchors
    assert r.pending(b) == 0

    _resolve(b, uid, "theirs")
    _push(b, store)
    _pull(a, store)
    assert _sections(a)["new"] == "новая секция B"
    _converged(a, b, store)


def test_custom_text_is_refused_for_delete_edit(pair: tuple) -> None:
    a, b, store = pair
    _common_doc(a, b, store)
    _delete_doc(a)
    _edit_section(b, "правка B")
    _push(a, store)
    (uid,) = _pull(b, store).conflicts
    with pytest.raises(ConflictResolveError), transactional(b.sf) as s:
        conflicts.resolve_conflict(s, b.project_id, uid, take="свой текст", author=r.AUTHOR)


def test_delete_without_clashing_tail_opens_nothing(pair: tuple) -> None:
    a, b, store = pair
    _common_doc(a, b, store)
    r.make_task(a, "DEM-001")
    _push(a, store)
    _pull(b, store)
    _delete_doc(a)
    r.set_description(b, "DEM-001", "правка задачи не под документом")
    _push(a, store)
    assert _pull(b, store).conflicts == []
    _push(b, store)
    _pull(a, store)
    _converged(a, b, store)


# --- правка первой ----------------------------------------------------------------


def test_edit_first_the_deleting_replica_opens_the_conflict(pair: tuple) -> None:
    """B пушит правку раньше; сливает A с удалением в хвосте — снимок из журнала A."""
    a, b, store = pair
    _common_doc(a, b, store)
    _delete_doc(a)
    _edit_section(b, "правка B")

    _push(b, store)
    pulled = _pull(a, store)

    (conflict,) = _open(a)
    assert pulled.conflicts == [conflict["uid"]]
    _assert_snapshot(conflict, "правка B")
    assert not _has_doc(a)
    _push(a, store)
    _pull(b, store)
    assert not _has_doc(b)  # удаление выиграло и на B
    assert _open(b) == []
    assert _digest(a) == _digest(b)

    _resolve(a, str(conflict["uid"]), "theirs")

    assert _sections(a) == {**SECTIONS, EDITED: "правка B"}
    _push(a, store)
    _pull(b, store)
    assert _sections(b) == {**SECTIONS, EDITED: "правка B"}
    _converged(a, b, store)


def test_edit_first_edit_of_the_deleted_entity_itself(pair: tuple) -> None:
    """Правка самого удалённого документа (не потомка) — тот же конфликт со снимком."""
    a, b, store = pair
    _common_doc(a, b, store)
    _delete_doc(a)
    time.sleep(_TICK)
    with transactional(b.sf) as s:
        cap.set_author(s, r.AUTHOR)
        doc = s.scalars(select(DocumentModel).where(DocumentModel.doc_key == DOC)).one()
        doc.title = "Заголовок B"
    _push(b, store)
    (uid,) = _pull(a, store).conflicts
    (conflict,) = _open(a)
    root = _snapshot_rows(conflict)[0]
    assert root["fields"]["title"] == "Заголовок B"  # type: ignore[index]
    _push(a, store)
    _pull(b, store)

    _resolve(a, uid, "theirs")
    _push(a, store)
    _pull(b, store)
    with transactional(b.sf, commit=False) as s:
        doc = s.scalars(select(DocumentModel).where(DocumentModel.doc_key == DOC)).one()
        assert doc.title == "Заголовок B"
    _converged(a, b, store)


# --- граница restricted ---------------------------------------------------------


def test_snapshot_journal_has_no_local_ops_after_merge(pair: tuple) -> None:
    """После слияния в хвосте B нет операций над поддеревом — ни upsert, ни delete."""
    a, b, store = pair
    _common_doc(a, b, store)
    r.make_task(a, "DEM-001")
    _push(a, store)
    _pull(b, store)
    _delete_doc(a)
    _edit_section(b, "правка B")
    r.set_description(b, "DEM-001", "правка задачи")
    _push(a, store)
    _pull(b, store)
    with transactional(b.sf, commit=False) as s:
        kinds = s.scalars(
            select(SyncOpModel.entity_kind).where(SyncOpModel.changeset.is_(None))
        ).all()
    assert "section" not in kinds
    assert "task" in kinds  # правка вне поддерева уезжает как обычно
    with transactional(b.sf, commit=False) as s:
        assert s.scalars(select(TaskModel.description)).one() == "правка задачи"


def test_partial_snapshot_row_is_refused_before_any_write(pair: tuple) -> None:
    """Строка, родившаяся до журнала, свёрнута не целиком: resolve theirs — SYNC-301."""
    a, _b, _store = pair
    row = {"table": "section", "uid": "0192-s", "fields": {"body": "x"}, "partial": True}
    spec = conflicts.ConflictSpec(
        entity_kind="document",
        entity_uid="0192-root",
        field=None,
        kind="delete_edit",
        ours={"delete_op": "01JDELETE", "edited": [["section", "0192-s"]]},
        theirs={"root": {"table": "document", "uid": "0192-root"}, "rows": [row]},
        base=None,
        theirs_op="01JTHEIRSOP0000000000000000",
    )
    with transactional(a.sf) as s:
        uid = conflicts.open_conflict(s, a.project_id, spec, author="system:sync")
    before = r.pending(a)
    with pytest.raises(ConflictResolveError) as err:
        _resolve(a, uid, "theirs")
    assert err.value.code == "SYNC-301"
    assert r.pending(a) == before
    assert [c["uid"] for c in _open(a)] == [uid]
