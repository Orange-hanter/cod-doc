"""COD-023: ProjectionService — export / detect_drift / import."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import projection_service as proj

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'proj.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def root_path(tmp_path: Path) -> Path:
    p = tmp_path / "mirror"
    p.mkdir()
    return p


def _seed_project(session: Session) -> int:
    now = datetime.now(timezone.utc)
    proj_model = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj_model.created = now; proj_model.updated = now
    session.add(proj_model); session.flush()
    return proj_model.row_id


def _make_doc(
    session: Session,
    project_id: int,
    *,
    doc_key: str = "test-doc",
    title: str = "Test Document",
    owner: str | None = "backend-team",
    status: DocumentStatus = DocumentStatus.ACTIVE,
) -> int:
    doc = docs.create(
        session, project_id=project_id, doc_key=doc_key,
        type=DocumentType.GUIDE, status=status,
        title=title, owner=owner, author="human:test",
    )
    return doc.row_id  # type: ignore[return-value]


# ============================================================================ #
# render_markdown                                                               #
# ============================================================================ #


def test_render_markdown_includes_frontmatter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        md = proj.render_markdown(session, doc_id)
        assert md.startswith("---\n")
        assert "type: guide" in md
        assert "status: active" in md
        assert "owner: backend-team" in md


def test_render_markdown_includes_section_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)
        docs.add_section(
            session, document_id=doc_id, anchor="intro",
            heading="Introduction", level=2, position=0,
            body="Hello from section.\n", author="human:test",
        )

        md = proj.render_markdown(session, doc_id)
        assert "## Introduction" in md
        assert "Hello from section." in md


def test_render_markdown_unknown_doc_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        with pytest.raises(docs.DocumentNotFoundError):
            proj.render_markdown(session, 9999)


# ============================================================================ #
# export_document                                                               #
# ============================================================================ #


def test_export_creates_file_and_updates_hash(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p, doc_key="modules/guide")

        result = proj.export_document(session, doc_id, root_path=root_path)

        assert result.written is True
        assert result.path == root_path / "modules/guide.md"
        assert result.path.exists()
        assert result.content_hash  # non-empty hash

        from cod_doc.infra.models import DocumentModel  # noqa: PLC0415
        doc_model = session.get(DocumentModel, doc_id)
        assert doc_model.projection_hash == result.content_hash


def test_export_skips_when_hash_matches(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        first = proj.export_document(session, doc_id, root_path=root_path)
        assert first.written is True

        second = proj.export_document(session, doc_id, root_path=root_path)
        assert second.written is False
        assert second.content_hash == first.content_hash


def test_export_force_rewrites_even_when_in_sync(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        proj.export_document(session, doc_id, root_path=root_path)
        result = proj.export_document(session, doc_id, root_path=root_path, force=True)
        assert result.written is True


def test_export_creates_parent_directories(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p, doc_key="a/b/c/nested")

        result = proj.export_document(session, doc_id, root_path=root_path)
        assert result.path.exists()
        assert result.path.parent.is_dir()


def test_export_reruns_after_content_change(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        first = proj.export_document(session, doc_id, root_path=root_path)

        # Modify the document; hash in DB should differ from projection_hash.
        docs.add_section(
            session, document_id=doc_id, anchor="new-section",
            heading="New", level=2, position=0,
            body="Added content.\n", author="human:test",
        )

        second = proj.export_document(session, doc_id, root_path=root_path)
        assert second.written is True
        assert second.content_hash != first.content_hash


# ============================================================================ #
# detect_drift                                                                  #
# ============================================================================ #


def test_detect_drift_missing_when_never_exported(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.MISSING


def test_detect_drift_in_sync_after_export(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        proj.export_document(session, doc_id, root_path=root_path)
        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.IN_SYNC


def test_detect_drift_stale_export_after_db_change(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        proj.export_document(session, doc_id, root_path=root_path)

        # Mutate DB without re-exporting.
        docs.add_section(
            session, document_id=doc_id, anchor="s", heading="S", level=2,
            position=0, body="X\n", author="human:test",
        )

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.STALE_EXPORT


def test_detect_drift_edited_in_place(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        result = proj.export_document(session, doc_id, root_path=root_path)

        # Simulate in-place edit of the markdown file.
        result.path.write_text(result.path.read_text() + "\n# Extra\n", encoding="utf-8")

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.EDITED_IN_PLACE


# ============================================================================ #
# import_document                                                               #
# ============================================================================ #


def test_import_returns_none_for_untracked_file(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        unknown = root_path / "unknown.md"
        unknown.write_text("---\ntype: guide\n---\n", encoding="utf-8")
        result = proj.import_document(session, p, unknown, author="human:test", root_path=root_path)
        assert result is None


def test_import_no_op_when_hash_matches(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        export_result = proj.export_document(session, doc_id, root_path=root_path)

        doc = proj.import_document(
            session, p, export_result.path,
            author="human:test", root_path=root_path,
        )
        assert doc is not None
        assert doc.row_id == doc_id


def test_import_applies_frontmatter_field_changes(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p, status=DocumentStatus.DRAFT)

        export_result = proj.export_document(session, doc_id, root_path=root_path)

        # Edit the file to change status from draft to active.
        old_content = export_result.path.read_text(encoding="utf-8")
        new_content = old_content.replace("status: draft", "status: active")
        export_result.path.write_text(new_content, encoding="utf-8")

        doc = proj.import_document(
            session, p, export_result.path,
            author="human:test", root_path=root_path,
        )
        assert doc is not None
        assert doc.status is DocumentStatus.ACTIVE
