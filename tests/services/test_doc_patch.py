"""COD-010 / RFL-073: DocService — patch_section."""

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


def test_patch_section_updates_body_and_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        sec = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="x",
            heading="X",
            level=2,
            position=0,
            body="old body",
            author="x",
        )

        patched = docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor="x",
            new_body="new body",
            author="human:dakh",
            reason="clarify",
        )
        assert patched.body == "new body"
        assert patched.content_hash == hashlib.sha256(b"new body").hexdigest()

        history = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)
        # add_section + patch_section → 2 revisions
        assert len(history) == 2
        assert history[1].parent_revision_id == history[0].revision_id
        assert "old body" in history[1].diff or "new body" in history[1].diff


def test_patch_section_unknown_anchor_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        with pytest.raises(docs.SectionNotFoundError):
            docs.patch_section(
                session,
                document_id=doc.row_id,
                anchor="ghost",
                new_body="x",
                author="x",
            )


def test_patch_section_unknown_document_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session, pytest.raises(docs.DocumentNotFoundError):
        docs.patch_section(session, document_id=99999, anchor="x", new_body="x", author="x")


def test_patch_section_no_op_when_body_unchanged(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Identical body must not bump revisions or content_hash."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        sec = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="x",
            heading="X",
            level=2,
            position=0,
            body="same",
            author="x",
        )

        result = docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor="x",
            new_body="same",
            author="x",
        )
        assert result.row_id == sec.row_id

        history = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)
        assert len(history) == 1  # only add_section's revision


def test_patch_section_optimistic_concurrency_conflict(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        sec = docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="x",
            heading="X",
            level=2,
            position=0,
            body="v1",
            author="x",
        )
        first_rev = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)[0]

        # Concurrent patch lands first.
        docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor="x",
            new_body="v2",
            author="other",
        )

        # We still think `first_rev` is head — must conflict.
        with pytest.raises(rev.RevisionConflictError):
            docs.patch_section(
                session,
                document_id=doc.row_id,
                anchor="x",
                new_body="v3",
                author="x",
                expected_parent_revision_id=first_rev.revision_id,
            )
