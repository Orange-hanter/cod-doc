"""COD-010: DocService — create / get / sections / patch / rename + revisions."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Sensitivity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'doc.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(timezone.utc)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _new_doc(session, project_id: int, doc_key: str = "modules/M1-auth/overview"):
    return docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="Auth Module Overview",
        author="human:dakh",
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
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        _new_doc(session, proj_id, "dup")

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            proj_id2 = (
                session.execute(
                    __import__("sqlalchemy").select(ProjectModel.row_id)
                ).scalar_one()
            )
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
            session, document_id=doc.row_id, anchor="b", heading="B", level=2,
            position=1, body="Bbody", author="x",
        )
        docs.add_section(
            session, document_id=doc.row_id, anchor="a", heading="A", level=2,
            position=0, body="Abody", author="x",
        )
        anchors = [s.anchor for s in docs.get_sections(session, doc.row_id)]
        assert anchors == ["a", "b"]


def test_render_body_uses_view(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        docs.add_section(
            session, document_id=doc.row_id, anchor="a", heading="Alpha", level=2,
            position=0, body="Alpha body.", author="x",
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
            session, document_id=doc.row_id, anchor="x", heading="X", level=2,
            position=0, body="old body", author="x",
        )

        patched = docs.patch_section(
            session, document_id=doc.row_id, anchor="x", new_body="new body",
            author="human:dakh", reason="clarify",
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
                session, document_id=doc.row_id, anchor="ghost",
                new_body="x", author="x",
            )


def test_patch_section_unknown_document_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        with pytest.raises(docs.DocumentNotFoundError):
            docs.patch_section(
                session, document_id=99999, anchor="x", new_body="x", author="x"
            )


def test_patch_section_no_op_when_body_unchanged(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Identical body must not bump revisions or content_hash."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)
        sec = docs.add_section(
            session, document_id=doc.row_id, anchor="x", heading="X", level=2,
            position=0, body="same", author="x",
        )

        result = docs.patch_section(
            session, document_id=doc.row_id, anchor="x", new_body="same",
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
            session, document_id=doc.row_id, anchor="x", heading="X", level=2,
            position=0, body="v1", author="x",
        )
        first_rev = rev.list_for_entity(session, EntityKind.SECTION, sec.row_id)[0]

        # Concurrent patch lands first.
        docs.patch_section(
            session, document_id=doc.row_id, anchor="x", new_body="v2", author="other",
        )

        # We still think `first_rev` is head — must conflict.
        with pytest.raises(rev.RevisionConflictError):
            docs.patch_section(
                session, document_id=doc.row_id, anchor="x", new_body="v3",
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

    with transactional(factory) as session:
        with pytest.raises(docs.DocumentNotFoundError):
            docs.rename(
                session, document_id=99999, new_doc_key="x", author="x"
            )


def test_rename_no_op_when_target_equals_current(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)

        result = docs.rename(
            session, document_id=doc.row_id, new_doc_key=doc.doc_key,
            new_path=doc.path, author="x",
        )
        assert result.row_id == doc.row_id

        history = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)
        assert len(history) == 1  # only create
