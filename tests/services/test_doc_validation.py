"""COD-010 / COD-020 / RFL-073: DocService — frontmatter gate + path validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


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
