"""COD-001 smoke: schema migration + minimal CRUD via repositories."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    Link,
    LinkKind,
    Project,
    Section,
    Sensitivity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import (
    DocumentRepository,
    ProjectRepository,
    SectionRepository,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {
        "PATH": "/usr/bin:/bin",
        "COD_DOC_DB_URL": db_url,
    }
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    db_file = tmp_path / "smoke.db"
    return f"sqlite:///{db_file}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def test_migration_creates_all_tables(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    with engine_with_schema.connect() as conn:
        names = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
    assert {"project", "document", "section", "link", "alembic_version"} <= names


def test_project_crud(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        repo = ProjectRepository(session)
        created = repo.add(
            Project(
                slug="restate",
                title="Restate",
                root_path="/Users/dakh/Git/Restate",
                config={"profile": "embedded"},
            )
        )
        assert created.row_id is not None

    with transactional(factory) as session:
        repo = ProjectRepository(session)
        fetched = repo.get_by_slug("restate")
        assert fetched is not None
        assert fetched.title == "Restate"
        assert fetched.config == {"profile": "embedded"}


def test_document_with_sections_and_links(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    # Setup: project + document + 2 sections + 1 link
    with transactional(factory) as session:
        proj = ProjectRepository(session).add(
            Project(slug="demo", title="Demo", root_path="/tmp/demo")
        )
        assert proj.row_id is not None

        doc = DocumentRepository(session).add(
            Document(
                project_id=proj.row_id,
                doc_key="modules/M1-auth/overview",
                path="docs/modules/M1-auth/overview.md",
                type=DocumentType.MODULE_SPEC,
                status=DocumentStatus.ACTIVE,
                title="Auth Module Overview",
                sensitivity=Sensitivity.INTERNAL,
                created=now,
                last_updated=now,
            )
        )
        assert doc.row_id is not None

        sec_repo = SectionRepository(session)
        s1 = sec_repo.add(
            Section(
                document_id=doc.row_id,
                anchor="overview",
                heading="Overview",
                level=2,
                position=0,
                body="One paragraph intro.",
                content_hash="hash1",
            )
        )
        sec_repo.add(
            Section(
                document_id=doc.row_id,
                anchor="data-model",
                heading="Data Model",
                level=2,
                position=1,
                body="Tables A, B, C...",
                content_hash="hash2",
            )
        )

        from cod_doc.infra.models import LinkModel  # noqa: PLC0415

        assert s1.row_id is not None
        session.add(
            LinkModel(
                project_id=proj.row_id,
                from_section_id=s1.row_id,
                raw="[[doc:modules/M1-billing/overview]]",
                kind=LinkKind.CANONICAL.value,
                to_doc_key="modules/M1-billing/overview",
                resolved=False,
            )
        )

    # Assert: read back through repos
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug("demo")
        assert proj is not None and proj.row_id is not None

        doc = DocumentRepository(session).get_by_key(proj.row_id, "modules/M1-auth/overview")
        assert doc is not None and doc.row_id is not None
        assert doc.sensitivity == Sensitivity.INTERNAL

        sections = SectionRepository(session).list_for_document(doc.row_id)
        assert [s.anchor for s in sections] == ["overview", "data-model"]


def test_unique_constraint_doc_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Same doc_key in same project must fail."""
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj = ProjectRepository(session).add(
            Project(slug="dup", title="Dup", root_path="/tmp/dup")
        )
        assert proj.row_id is not None
        DocumentRepository(session).add(
            Document(
                project_id=proj.row_id,
                doc_key="x",
                path="x.md",
                type=DocumentType.GUIDE,
                status=DocumentStatus.DRAFT,
                title="X",
                created=now,
                last_updated=now,
            )
        )

    with pytest.raises(IntegrityError):
        with transactional(factory) as session:
            proj_id = ProjectRepository(session).get_by_slug("dup").row_id  # type: ignore[union-attr]
            DocumentRepository(session).add(
                Document(
                    project_id=proj_id,
                    doc_key="x",
                    path="x2.md",
                    type=DocumentType.GUIDE,
                    status=DocumentStatus.DRAFT,
                    title="X again",
                    created=now,
                    last_updated=now,
                )
            )


def test_cascade_delete_project_drops_documents(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(timezone.utc)

    with transactional(factory) as session:
        proj = ProjectRepository(session).add(
            Project(slug="todrop", title="To Drop", root_path="/tmp/todrop")
        )
        assert proj.row_id is not None
        DocumentRepository(session).add(
            Document(
                project_id=proj.row_id,
                doc_key="d",
                path="d.md",
                type=DocumentType.GUIDE,
                status=DocumentStatus.DRAFT,
                title="D",
                created=now,
                last_updated=now,
            )
        )

    with transactional(factory) as session:
        from cod_doc.infra.models import ProjectModel  # noqa: PLC0415

        # Quick-and-dirty drop via ORM to validate cascade.
        proj_model = session.get(ProjectModel, sys.maxsize)  # not exists, just to import
        del proj_model
        target = session.query(__import__("cod_doc.infra.models", fromlist=["ProjectModel"]).ProjectModel).filter_by(slug="todrop").one()
        session.delete(target)

    with transactional(factory) as session:
        from sqlalchemy import select as _select  # noqa: PLC0415

        from cod_doc.infra.models import DocumentModel  # noqa: PLC0415

        remaining = session.execute(_select(DocumentModel).where(DocumentModel.path == "d.md")).first()
        assert remaining is None
