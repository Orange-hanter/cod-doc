"""WEB-003: docs list + show through DocService over the embedded sqlite DB."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Sensitivity,
)
from cod_doc.domain.entities import (
    Project as ProjectEntity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def docs_client(tmp_path: Path, migrate_db):
    """Project with `.cod-doc/state.db` migrated and seeded."""
    repo = tmp_path / "demo-repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    # Seed: ProjectModel with matching slug + one document with two sections.
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        doc = docs.create(
            session,
            project_id=proj.row_id,
            doc_key="modules/M1-auth/overview",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="Auth Module Overview",
            author="human:dakh",
            owner="human:dakh",
            sensitivity=Sensitivity.INTERNAL,
            preamble="Top preamble.",
        )
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="data-model",
            heading="Data Model",
            level=2,
            position=0,
            body="Entities and tables.",
            author="human:dakh",
        )
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor="api",
            heading="API",
            level=2,
            position=1,
            body="Endpoints.",
            author="human:dakh",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_docs_list_renders_seeded_doc(docs_client) -> None:
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs")
    assert r.status_code == 200
    assert "modules/M1-auth/overview" in r.text
    assert "Auth Module Overview" in r.text
    assert "module-spec" in r.text
    assert "active" in r.text
    # Tab strip: Docs is the active one
    assert 'class="active" href="/p/demo/docs"' in r.text


def test_docs_list_warns_when_db_absent(tmp_path: Path) -> None:
    """No `.cod-doc/state.db` → page renders with a 'not initialized' notice."""
    repo = tmp_path / "no-db"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/docs")
    assert r.status_code == 200
    assert "DB-проект не инициализирован" in r.text


def test_doc_show_renders_sections_and_body(docs_client) -> None:
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1-auth/overview")
    assert r.status_code == 200
    assert "Auth Module Overview" in r.text
    # sections nav
    assert 'href="#data-model"' in r.text
    assert 'href="#api"' in r.text
    assert "Data Model" in r.text
    # body via document_body view: preamble + section bodies
    assert "Top preamble." in r.text
    assert "Entities and tables." in r.text
    assert "Endpoints." in r.text


def test_doc_show_404_when_doc_missing(docs_client) -> None:
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs/no/such/doc")
    assert r.status_code == 404


def test_doc_show_404_when_db_absent(tmp_path: Path) -> None:
    repo = tmp_path / "no-db2"
    repo.mkdir()
    entry = ProjectEntry(name="bare2", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/docs/anything")
    assert r.status_code == 404


# ── COD-052: doc accept flow ────────────────────────────────────────────


def test_doc_show_renders_accept_button_when_draft(docs_client) -> None:
    """Draft docs surface an 'Accept' button; active docs do not."""
    client, entry = docs_client
    # Seed a DRAFT doc.
    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(entry.name)
        docs.create(
            session,
            project_id=proj.row_id,
            doc_key="drafts/spec",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="Draft spec",
            author="human:dakh",
            owner="human:dakh",
            sensitivity=Sensitivity.INTERNAL,
        )
    engine.dispose()

    r = client.get(f"/p/{entry.name}/docs/drafts/spec")
    assert "Accept (status → active)" in r.text
    assert f'/p/{entry.name}/docs-accept' in r.text

    # ACTIVE doc seeded by fixture should NOT have the button.
    r2 = client.get(f"/p/{entry.name}/docs/modules/M1-auth/overview")
    assert "Accept (status → active)" not in r2.text


def test_doc_accept_endpoint_promotes_status(docs_client) -> None:
    client, entry = docs_client
    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(entry.name)
        docs.create(
            session,
            project_id=proj.row_id,
            doc_key="drafts/another",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="Another",
            author="human:dakh",
            owner="human:dakh",
            sensitivity=Sensitivity.INTERNAL,
        )
    engine.dispose()

    r = client.post(
        f"/p/{entry.name}/docs-accept",
        data={"doc_key": "drafts/another"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/docs/drafts/another")

    # The doc now reports ACTIVE on its detail page.
    follow = client.get(f"/p/{entry.name}/docs/drafts/another")
    assert "badge-active" in follow.text


def test_doc_accept_endpoint_400_on_missing_doc_key(docs_client) -> None:
    client, entry = docs_client
    r = client.post(f"/p/{entry.name}/docs-accept", data={})
    assert r.status_code == 400


def test_doc_accept_endpoint_404_on_unknown_doc(docs_client) -> None:
    client, entry = docs_client
    r = client.post(
        f"/p/{entry.name}/docs-accept",
        data={"doc_key": "ghost"},
        follow_redirects=False,
    )
    assert r.status_code == 404
