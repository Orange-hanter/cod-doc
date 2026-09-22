"""ADO-211: документ находится поиском сразу после записи, без ``reindex_all``.

До фикса индексировал только ``import_or_update_markdown``. ``doc_service``
(create / add_section / patch_section / rename) и ``import_markdown`` писали
документ мимо FTS-индекса: документ, созданный через MCP/CLI ``doc_create``,
не находился, пока индекс непустой — ``ensure_index`` переиндексирует только
пустой. Так RFC 26 оказался в БД, но не в поиске (171 документ против 170).

Здесь ``search`` зовётся напрямую: он, в отличие от MCP ``ctx_search``, не
вызывает ``ensure_index``, поэтому тест видит ровно то, что сделал write-путь.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service, import_service, search_service

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Document

AUTHOR = "human:test"


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="demo", title="Demo", root_path="/tmp/demo", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _doc(session: Session, project_id: int, *, doc_key: str, title: str) -> Document:
    return doc_service.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.GUIDE,
        status=DocumentStatus.DRAFT,
        title=title,
        author=AUTHOR,
    )


def _refs(session: Session, project_id: int, query: str) -> list[Any]:
    hits = search_service.search(session, project_id=project_id, query=query, scope="doc")
    return [h["ref"] for h in hits["by_kind"]["doc"]]


def test_create_makes_doc_searchable(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _seed_project(session)
        _doc(session, pid, doc_key="guides/quasar", title="Quasar calibration")

        assert _refs(session, pid, "quasar") == ["guides/quasar"]


def test_add_section_indexes_the_section_body(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _seed_project(session)
        doc = _doc(session, pid, doc_key="guides/ops", title="Operations")
        assert doc.row_id is not None
        doc_service.add_section(
            session,
            document_id=doc.row_id,
            anchor="drain",
            heading="Drain",
            level=2,
            position=0,
            body="Zephyrine drains the queue before rollout.",
            author=AUTHOR,
        )

        assert _refs(session, pid, "zephyrine") == ["guides/ops"]


def test_patch_section_reindexes_the_new_body(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _seed_project(session)
        doc = _doc(session, pid, doc_key="guides/ops", title="Operations")
        assert doc.row_id is not None
        doc_service.add_section(
            session,
            document_id=doc.row_id,
            anchor="drain",
            heading="Drain",
            level=2,
            position=0,
            body="Old wording about the queue.",
            author=AUTHOR,
        )
        doc_service.patch_section(
            session,
            document_id=doc.row_id,
            anchor="drain",
            new_body="Obsidian wording replaces it.",
            author=AUTHOR,
        )

        assert _refs(session, pid, "obsidian") == ["guides/ops"]
        assert _refs(session, pid, "old") == []


def test_rename_moves_the_index_row_to_the_new_key(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _seed_project(session)
        doc = _doc(session, pid, doc_key="guides/old-name", title="Heliotrope notes")
        assert doc.row_id is not None
        doc_service.rename(
            session,
            document_id=doc.row_id,
            new_doc_key="guides/new-name",
            author=AUTHOR,
        )

        assert _refs(session, pid, "heliotrope") == ["guides/new-name"]


def test_import_markdown_makes_doc_searchable(engine_with_schema: Engine) -> None:
    raw = "---\ntitle: Tessellate Runbook\ntype: guide\n---\n# Tessellate Runbook\n\n## Steps\n\nPlain.\n"
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _seed_project(session)
        import_service.import_markdown(
            session,
            project_id=pid,
            doc_key="guides/tessellate",
            raw_markdown=raw,
            author=AUTHOR,
        )

        assert _refs(session, pid, "tessellate") == ["guides/tessellate"]
