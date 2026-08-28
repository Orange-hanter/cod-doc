"""ADO-031: DocService — document deletion (single + bulk candidates)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    Sensitivity,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DocumentModel,
    LinkModel,
    ProjectModel,
    SectionModel,
)
from cod_doc.services import doc_service as docs
from cod_doc.services import search_service
from cod_doc.services.link_service import sync_section

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_project(session: Session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _new_doc(
    session: Session,
    project_id: int,
    doc_key: str,
    *,
    path: str | None = None,
    doc_type: DocumentType = DocumentType.MODULE_SPEC,
    title: str = "Title",
) -> Document:
    return docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=doc_type,
        status=DocumentStatus.ACTIVE,
        title=title,
        author="human:dakh",
        owner="human:dakh",
        sensitivity=Sensitivity.INTERNAL,
        path=path,
        preamble="preamble",
    )


def test_delete_removes_doc_sections_links_and_fts_emits_event(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        target = _new_doc(session, proj_id, "target-doc", title="Target")
        doc = _new_doc(session, proj_id, "doc-to-delete", title="Delete Me")
        section = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=1,
            position=0,
            body="See [[target-doc]] for context.",
            author="human:dakh",
        )
        sync_section(session, section.row_id)
        search_service.upsert_doc(session, project_id=proj_id, doc_key=doc.doc_key)
        search_service.upsert_doc(session, project_id=proj_id, doc_key=target.doc_key)

    with transactional(factory) as session:
        result = docs.delete(session, project_id=proj_id, doc_key="doc-to-delete", author="cli")
        assert result.doc_key == "doc-to-delete"
        assert result.title == "Delete Me"
        assert result.section_count == 1

    with transactional(factory) as session:
        assert session.get(DocumentModel, doc.row_id) is None
        assert (
            session.execute(
                select(SectionModel).where(SectionModel.document_id == doc.row_id)
            ).scalar_one_or_none()
            is None
        )
        assert (
            session.execute(
                select(LinkModel).where(LinkModel.from_section_id == section.row_id)
            ).scalar_one_or_none()
            is None
        )

        # FTS row for the deleted document is gone; the other document remains.
        fts = list(
            session.execute(
                text("SELECT kind, ref FROM db_search_idx WHERE project_id = :p"),
                {"p": proj_id},
            ).all()
        )
        assert {r.ref for r in fts} == {"target-doc"}

        # Revisions are append-only and survive.
        doc_revs = list(
            session.execute(
                text(
                    "SELECT revision_id FROM revision "
                    "WHERE project_id = :p AND entity_kind = 'document' AND entity_id = :eid"
                ),
                {"p": proj_id, "eid": doc.row_id},
            ).all()
        )
        assert len(doc_revs) == 1

        event = session.execute(
            select(ActivityEventModel).where(
                ActivityEventModel.project_id == proj_id,
                ActivityEventModel.kind == "doc.deleted",
                ActivityEventModel.scope_id == "doc-to-delete",
            )
        ).scalar_one()
        assert event.payload["doc_key"] == "doc-to-delete"
        assert event.payload["section_count"] == 1


def test_delete_unknown_document_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)

    with transactional(factory) as session, pytest.raises(docs.DocumentNotFoundError):
        docs.delete(session, project_id=proj_id, doc_key="missing", author="cli")


def test_list_delete_candidates_by_path_glob_and_type(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        _new_doc(
            session,
            proj_id,
            "exp-a",
            path="experiments/a.md",
            doc_type=DocumentType.JOURNAL,
            title="Journal A",
        )
        _new_doc(
            session,
            proj_id,
            "exp-b",
            path="experiments/b.md",
            doc_type=DocumentType.JOURNAL,
            title="Journal B",
        )
        _new_doc(
            session,
            proj_id,
            "design-c",
            path="experiments/c-design.md",
            doc_type=DocumentType.DESIGN,
            title="Design C",
        )

    with transactional(factory) as session:
        all_exp = docs.list_delete_candidates(
            session, project_id=proj_id, path_glob="experiments/**"
        )
        assert {c.doc_key for c in all_exp} == {"design-c", "exp-a", "exp-b"}

        journals = docs.list_delete_candidates(
            session, project_id=proj_id, path_glob="experiments/**", doc_type="journal"
        )
        assert {c.doc_key for c in journals} == {"exp-a", "exp-b"}

        none = docs.list_delete_candidates(session, project_id=proj_id, path_glob="nope/**")
        assert none == []
