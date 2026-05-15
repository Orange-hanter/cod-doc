"""COD-010 / RFL-073: DocService — create."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    EntityKind,
    Sensitivity,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _new_doc(
    session: Session, project_id: int, doc_key: str = "modules/M1-auth/overview"
) -> Document:
    return docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="Auth Module Overview",
        author="human:dakh",
        owner="human:dakh",
        sensitivity=Sensitivity.INTERNAL,
        preamble="Intro paragraph.",
    )


def test_create_persists_document_with_defaults(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)

    with transactional(factory) as session:
        loaded = docs.get(session, proj_id, "modules/M1-auth/overview")
        assert loaded is not None
        assert loaded.row_id == doc.row_id
        assert loaded.path == "modules/M1-auth/overview.md"  # default
        assert loaded.title == "Auth Module Overview"
        assert loaded.preamble == "Intro paragraph."
        assert loaded.frontmatter == {}


def test_create_writes_initial_document_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        history = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)

        assert len(history) == 1
        assert history[0].parent_revision_id is None
        assert history[0].author == "human:dakh"
        assert "Intro paragraph." in history[0].diff


def test_create_duplicate_doc_key_violates_unique(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        _new_doc(session, proj_id, "dup")

    with pytest.raises(IntegrityError), transactional(factory) as session:
        proj_id2 = session.execute(
            __import__("sqlalchemy").select(ProjectModel.row_id)
        ).scalar_one()
        _new_doc(session, proj_id2, "dup")  # same project, same key
