"""COD-010 / RFL-073: DocService — add_section / get_sections / render_body."""

from __future__ import annotations

import hashlib
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


def test_add_section_writes_section_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        sec = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="overview",
            heading="Overview",
            level=2,
            position=0,
            body="One paragraph.",
            author="agent:task-steward",
        )
        assert sec.row_id is not None
        assert sec.content_hash == hashlib.sha256(b"One paragraph.").hexdigest()

        history = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)
        assert len(history) == 1


def test_get_sections_in_position_order(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        # Insert out of order — get_sections must return by position.
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="b",
            heading="B",
            level=2,
            position=1,
            body="Bbody",
            author="x",
        )
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="a",
            heading="A",
            level=2,
            position=0,
            body="Abody",
            author="x",
        )
        anchors = [s.anchor for s in docs.get_sections(session, doc.row_id)]
        assert anchors == ["a", "b"]


def test_render_body_uses_view(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="a",
            heading="Alpha",
            level=2,
            position=0,
            body="Alpha body.",
            author="x",
        )
        body = docs.render_body(session, doc.row_id)
        assert body is not None
        assert body.startswith("Intro paragraph.")
        assert "## Alpha\n\nAlpha body." in body


def test_render_body_returns_none_for_unknown_doc(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        assert docs.render_body(session, 99999) is None


def test_add_section_duplicate_anchor_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.services.doc_service import SectionAlreadyExistsError

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="dup",
            heading="Dup",
            level=2,
            position=0,
            body="x",
            author="x",
        )
        with pytest.raises(SectionAlreadyExistsError):
            docs.add_section(
                session,
                document_id=doc.row_id,
                anchor="dup",
                heading="Dup2",
                level=2,
                position=1,
                body="y",
                author="x",
            )
