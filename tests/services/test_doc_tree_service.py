"""ADO-116: дерево документации — разделы, раскладка, Инбокс."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, DocumentModel, ProjectModel, RevisionModel
from cod_doc.services import doc_tree_service as tree

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _doc(
    session: Session,
    project_id: int,
    doc_key: str,
    doc_type: DocumentType = DocumentType.MODULE_SPEC,
) -> DocumentModel:
    now = datetime.now(UTC)
    model = DocumentModel(
        project_id=project_id,
        doc_key=doc_key,
        path=f"{doc_key}.md",
        type=doc_type.value,
        status=DocumentStatus.DRAFT.value,
        title=doc_key,
        created=now,
        last_updated=now,
    )
    session.add(model)
    session.flush()
    return model


@pytest.fixture
def session_factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _revisions(session: Session) -> int:
    """Число ревизий с явным flush.

    ``make_session_factory`` ставит ``autoflush=False``, поэтому ``count()``
    не видит строк, добавленных последней мутацией. Без flush тест на
    идемпотентность зеленел бы и в том случае, если бы повторный вызов писал
    ревизию: оба замера были бы одинаково слепыми.
    """
    session.flush()
    return int(session.query(RevisionModel).count())


def test_init_tree_seeds_default_nodes_and_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        created = tree.init_tree(session, project_id=pid, author="human:test")
        assert created, "дефолтное дерево должно засеяться"
        first = {n.node_key for n in tree.list_nodes(session, pid)}

        again = tree.init_tree(session, project_id=pid, author="human:test")
        assert again == [], "повторный сев не должен ничего создавать"
        assert {n.node_key for n in tree.list_nodes(session, pid)} == first

        inbox = tree.inbox_node(session, pid)
        assert inbox is not None
        assert inbox.is_inbox is True


def test_every_node_carries_intent(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Раздел без намерения — голый ярлык: «что сюда класть» приходится гадать."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        empty = [n.node_key for n in tree.list_nodes(session, pid) if not n.intent.strip()]
        assert not empty, f"разделы без intent: {empty}"


def test_assign_moves_document_and_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)

        node = tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )
        assert node is not None and node.node_key == "vision"
        assert tree.unplaced(session, pid) == []

        before = _revisions(session)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )
        assert _revisions(session) == before, (
            "повторная привязка к тому же разделу не должна писать ревизию"
        )


def test_assign_none_returns_document_to_inbox(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )

        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key=None,
            author="human:test",
        )
        assert tree.unplaced(session, pid) == ["docs/system/VISION"]
        assert tree.unplaced_count(session, pid) == 1


def test_assign_rejects_unknown_node(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "a")
        with pytest.raises(tree.NodeNotFoundError):
            tree.assign(session, project_id=pid, doc_key="a", node_key="nope", author="human:test")


def test_classify_dry_run_writes_nothing(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        _doc(session, pid, "docs/system/audit/2026-01-01-x", DocumentType.AUDIT_REPORT)

        report = tree.classify_project(session, project_id=pid, author="human:test", dry_run=True)
        assert report.by_node == {"vision": 1, "audit": 1}
        assert tree.unplaced_count(session, pid) == 2, "dry-run не должен ничего перекладывать"


def test_classify_applies_and_leaves_the_rest_unplaced(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        _doc(session, pid, "session-log", DocumentType.MODULE_SPEC)

        report = tree.classify_project(session, project_id=pid, author="human:test", dry_run=False)
        assert [p.doc_key for p in report.unplaced] == ["session-log"]
        assert tree.unplaced(session, pid) == ["session-log"]


def test_classify_does_not_move_what_a_human_placed(session_factory) -> None:  # type: ignore[no-untyped-def]
    """``only_unplaced`` по умолчанию: повторный прогон не спорит с человеком."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="audit",
            author="human:test",
        )

        tree.classify_project(session, project_id=pid, author="human:test", dry_run=False)
        doc = (
            session.query(DocumentModel).filter(DocumentModel.doc_key == "docs/system/VISION").one()
        )
        placed = tree.get_node(session, pid, "audit")
        assert placed is not None
        assert doc.node_id == placed.row_id, "ручная раскладка не должна переписываться"


def test_classify_skips_a_node_the_project_never_created(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Правило указывает на раздел, которого нет — документ остаётся в Инбоксе."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.create_node(
            session,
            project_id=pid,
            node_key="inbox",
            title="Инбокс",
            intent="очередь разбора",
            is_inbox=True,
            author="human:test",
        )
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)

        report = tree.classify_project(session, project_id=pid, author="human:test", dry_run=True)
        assert report.by_node == {}
        assert "не заведён" in report.unplaced[0].reason


def test_delete_node_refuses_to_silently_drop_documents(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )

        with pytest.raises(tree.NodeHasDocumentsError):
            tree.delete_node(session, project_id=pid, node_key="vision", author="human:test")

        moved = tree.delete_node(
            session, project_id=pid, node_key="vision", reassign_to="audit", author="human:test"
        )
        assert moved == 1
        audit = tree.get_node(session, pid, "audit")
        assert audit is not None
        doc = (
            session.query(DocumentModel).filter(DocumentModel.doc_key == "docs/system/VISION").one()
        )
        assert doc.node_id == audit.row_id


def test_delete_node_force_sends_documents_to_inbox(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )

        assert (
            tree.delete_node(
                session, project_id=pid, node_key="vision", force=True, author="human:test"
            )
            == 1
        )
        assert tree.unplaced(session, pid) == ["docs/system/VISION"]


def test_update_node_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        tree.update_node(
            session, project_id=pid, node_key="audit", title="Аудит", author="human:test"
        )
        before = _revisions(session)
        tree.update_node(
            session, project_id=pid, node_key="audit", title="Аудит", author="human:test"
        )
        assert _revisions(session) == before


def test_node_stats_flag_an_under_filled_node(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        stats = {s.node.node_key: s for s in tree.node_stats(session, pid)}
        assert stats["vision"].under_filled is True, "min_docs=1 и ни одного документа"
        assert stats["audit"].under_filled is False, "min_docs=0 — пустой аудит законен"


def test_mutations_leave_a_trail(session_factory) -> None:
    """ADO-040: каждая мутация обязана писать и ревизию, и событие."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )

        # autoflush=False (infra/db.py): без явного flush запрос не увидит
        # ни ревизию, ни событие, добавленные последней мутацией.
        session.flush()

        kinds = {r.entity_kind for r in session.query(RevisionModel).all()}
        assert EntityKind.DOC_NODE.value in kinds
        assert EntityKind.DOCUMENT.value in kinds

        events = {e.kind for e in session.query(ActivityEventModel).all()}
        assert {"doc.node_created", "doc.node_changed"} <= events


def test_inbox_is_one_state_not_two(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Явная привязка к Инбоксу — это тот же ``node_id IS NULL``.

    Иначе документ попадал бы в Инбокс двумя способами, и счётчик рельса
    пришлось бы складывать из двух источников.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )

        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="inbox",
            author="human:test",
        )
        doc = (
            session.query(DocumentModel).filter(DocumentModel.doc_key == "docs/system/VISION").one()
        )
        assert doc.node_id is None
        assert tree.unplaced(session, pid) == ["docs/system/VISION"]

        stats = {s.node.node_key: s.doc_count for s in tree.node_stats(session, pid)}
        assert stats["inbox"] == 1, "узел Инбокса показывает неразложенные"
        assert stats["vision"] == 0


def test_assign_does_not_touch_content_freshness(session_factory) -> None:  # type: ignore[no-untyped-def]
    """``last_updated`` — отметка о свежести содержимого, не о раскладке.

    По ней человек читает колонку «обновлён», а правила FM-004/FM-005 — когда
    документ протух. ``classify --apply`` проходит разом по всему корпусу:
    если бы раскладка её двигала, один вызов обнулил бы признак протухания у
    всех документов сразу.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        doc = _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        stamp = doc.last_updated

        tree.classify_project(session, project_id=pid, author="human:test", dry_run=False)
        session.flush()

        refreshed = (
            session.query(DocumentModel).filter(DocumentModel.doc_key == "docs/system/VISION").one()
        )
        assert refreshed.node_id is not None, "документ должен быть разложен"
        assert refreshed.last_updated == stamp


def test_delete_node_reassigning_to_inbox_lands_in_null(session_factory) -> None:  # type: ignore[no-untyped-def]
    """``--reassign-to inbox`` обязан нормализоваться в ``node_id IS NULL``.

    Иначе документы уезжают на строку Инбокса и пропадают из всех счётчиков
    сразу: рельс считает Инбокс по NULL и таких строк не видит. Поймано на
    живом корпусе — 42 документа стали невидимы после одного вызова.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/system/VISION", DocumentType.VISION)
        tree.assign(
            session,
            project_id=pid,
            doc_key="docs/system/VISION",
            node_key="vision",
            author="human:test",
        )

        moved = tree.delete_node(
            session,
            project_id=pid,
            node_key="vision",
            reassign_to="inbox",
            author="human:test",
        )
        assert moved == 1

        doc = (
            session.query(DocumentModel).filter(DocumentModel.doc_key == "docs/system/VISION").one()
        )
        assert doc.node_id is None
        assert tree.unplaced(session, pid) == ["docs/system/VISION"]
        stats = {s.node.node_key: s.doc_count for s in tree.node_stats(session, pid)}
        assert stats["inbox"] == 1


def test_every_document_is_counted_exactly_once(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Сумма по рельсу равна корпусу: документ не может выпасть из счёта."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        for key, kind in (
            ("docs/system/VISION", DocumentType.VISION),
            ("docs/system/audit/2026-01-01-x", DocumentType.AUDIT_REPORT),
            ("session-log", DocumentType.MODULE_SPEC),
        ):
            _doc(session, pid, key, kind)
        tree.classify_project(session, project_id=pid, author="human:test", dry_run=False)

        total = sum(stat.doc_count for stat in tree.node_stats(session, pid))
        assert total == 3
