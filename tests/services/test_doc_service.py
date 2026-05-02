"""COD-010: DocService — create / get / sections / patch / rename + revisions."""

from __future__ import annotations

import hashlib
import json
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


# --------------------------- create -----------------------------------------


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


# --------------------------- sections / render -------------------------------


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


# --------------------------- patch_section ----------------------------------


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


# --------------------------- rename ------------------------------------------


def test_rename_updates_doc_key_and_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)

        renamed = docs.rename(
            session,
            document_id=doc.row_id,
            new_doc_key="modules/M1-auth/spec",
            author="human:dakh",
        )
        assert renamed.doc_key == "modules/M1-auth/spec"
        assert renamed.path == "modules/M1-auth/spec.md"

        history = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)
        assert len(history) == 2  # create + rename
        rename_rev = history[1]
        payload = json.loads(rename_rev.diff)
        assert payload["op"] == "rename"
        assert payload["from"]["doc_key"] == "modules/M1-auth/overview"
        assert payload["to"]["doc_key"] == "modules/M1-auth/spec"


def test_rename_unknown_document_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session, pytest.raises(docs.DocumentNotFoundError):
        docs.rename(session, document_id=99999, new_doc_key="x", author="x")


def test_rename_no_op_when_target_equals_current(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)

        result = docs.rename(
            session,
            document_id=doc.row_id,
            new_doc_key=doc.doc_key,
            new_path=doc.path,
            author="x",
        )
        assert result.row_id == doc.row_id

        history = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)
        assert len(history) == 1  # only create


# -------------- SB-LO-7: add_section duplicate anchor -----------------------


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


# ----------------- COD-020: write-path frontmatter gate ---------------------


def test_create_rejects_active_without_owner(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """FM-002: status=active requires non-empty owner."""
    from cod_doc.services.validation import ValidationError

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        with pytest.raises(ValidationError) as exc_info:
            docs.create(
                session,
                project_id=proj_id,
                doc_key="modules/M1-auth/overview",
                type=DocumentType.MODULE_SPEC,
                status=DocumentStatus.ACTIVE,
                title="Auth Overview",
                author="human:dakh",
                # owner is intentionally omitted
            )
        assert exc_info.value.code == "FM-002"


def test_create_rejects_sot_false_without_canonical_source(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """FM-003: source_of_truth=false requires a canonical_source field."""
    from cod_doc.services.validation import ValidationError

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        with pytest.raises(ValidationError) as exc_info:
            docs.create(
                session,
                project_id=proj_id,
                doc_key="modules/copy/overview",
                type=DocumentType.GUIDE,
                status=DocumentStatus.DRAFT,
                title="Mirror",
                author="human:dakh",
                frontmatter={"source_of_truth": False},
            )
        assert exc_info.value.code == "FM-003"


def test_create_accepts_draft_without_owner(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """status=draft has no owner requirement (FM-002 is active-only)."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = docs.create(
            session,
            project_id=proj_id,
            doc_key="drafts/note",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="Note",
            author="human:dakh",
        )
        assert doc.row_id is not None


def test_create_rejects_absolute_path(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """SD-100: doc_service.create must reject absolute paths (path traversal guard)."""
    from cod_doc.services import validation

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        with pytest.raises(validation.ValidationError) as exc:
            docs.create(
                session,
                project_id=proj_id,
                doc_key="evil",
                type=DocumentType.GUIDE,
                status=DocumentStatus.DRAFT,
                title="evil",
                author="human:test",
                path="/etc/passwd",
            )
        assert exc.value.code == "SD-100"


def test_create_rejects_traversal_in_default_path(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """If path is not given, default `<doc_key>.md` is also validated."""
    from cod_doc.services import validation

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        with pytest.raises(validation.ValidationError) as exc:
            docs.create(
                session,
                project_id=proj_id,
                doc_key="../../etc/passwd",
                type=DocumentType.GUIDE,
                status=DocumentStatus.DRAFT,
                title="evil",
                author="human:test",
            )
        assert exc.value.code == "SD-100"


def test_rename_rejects_absolute_new_path(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """SD-100: doc_service.rename must reject absolute new_path."""
    from cod_doc.services import validation

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = docs.create(
            session,
            project_id=proj_id,
            doc_key="ok",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="ok",
            author="human:test",
        )
        assert doc.row_id is not None
        with pytest.raises(validation.ValidationError) as exc:
            docs.rename(
                session,
                document_id=doc.row_id,
                new_doc_key="ok-renamed",
                new_path="/etc/passwd",
                author="human:test",
            )
        assert exc.value.code == "SD-100"
