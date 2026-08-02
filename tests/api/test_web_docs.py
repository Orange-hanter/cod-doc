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


# ── COD-078: redesigned docs page ──────────────────────────────────────


def test_docs_list_renders_tree_view_by_default(docs_client) -> None:
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs")
    body = r.text
    # Toolbar with three create actions and an import section.
    assert "New blank" in body
    assert "Generate via AI" in body
    assert "Import markdown" in body
    # Filter bar
    assert 'name="q"' in body
    # Tree node for the seeded doc's first path component
    assert 'class="docs-folder"' in body
    assert "modules" in body
    # Counts row
    assert "active" in body and "draft" in body


def test_docs_list_search_filter_drops_non_matches(docs_client) -> None:
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs?q=auth")
    assert r.status_code == 200
    assert "modules/M1-auth/overview" in r.text

    r2 = client.get(f"/p/{entry.name}/docs?q=does-not-exist")
    assert r2.status_code == 200
    assert "Под фильтр ничего не попало" in r2.text


def test_docs_list_status_filter_only_active(docs_client) -> None:
    client, entry = docs_client
    # Seed an extra DRAFT doc so the filter has something to remove.
    db_path = entry.cod_doc_dir / "state.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(entry.name)
        docs.create(
            session,
            project_id=proj.row_id,
            doc_key="drafts/extra",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="Extra",
            author="human:dakh",
            owner="human:dakh",
            sensitivity=Sensitivity.INTERNAL,
        )
    engine.dispose()

    r = client.get(f"/p/{entry.name}/docs?status=active")
    assert "modules/M1-auth/overview" in r.text
    assert "drafts/extra" not in r.text


def test_doc_new_form_renders(docs_client) -> None:
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs/new")
    assert r.status_code == 200
    assert 'name="doc_key"' in r.text
    assert 'name="preamble"' in r.text


def test_doc_new_creates_and_redirects(docs_client) -> None:
    client, entry = docs_client
    r = client.post(
        f"/p/{entry.name}/docs/new",
        data={
            "doc_key": "guides/onboarding",
            "title": "Onboarding",
            "type": "guide",
            "status": "draft",
            "sensitivity": "internal",
            "owner": "human:dakh",
            "preamble": "Welcome.",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].endswith("/docs/guides/onboarding")

    follow = client.get(f"/p/{entry.name}/docs/guides/onboarding")
    assert "Onboarding" in follow.text


def test_doc_new_rejects_duplicate_key(docs_client) -> None:
    client, entry = docs_client
    r = client.post(
        f"/p/{entry.name}/docs/new",
        data={
            "doc_key": "modules/M1-auth/overview",  # already seeded
            "title": "Dup",
            "type": "module-spec",
            "status": "draft",
            "sensitivity": "internal",
            "owner": "",
            "preamble": "",
        },
        follow_redirects=False,
    )
    assert r.status_code in (303, 422)
    if r.status_code == 303:
        # Falls back to flash-cookie error redirect — location may be
        # the Referer or "/" when no Referer is set in the test client.
        assert r.cookies.get("flash_message")


def test_doc_generate_form_lists_sources(docs_client) -> None:
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs/generate")
    body = r.text
    assert "Generate document via AI" in body
    assert "modules/M1-auth/overview" in body
    assert 'name="source"' in body
    assert 'name="intent"' in body


def test_doc_generate_preview_then_save(docs_client, monkeypatch) -> None:
    """Mock ai_generate.generate_doc_from_sources, run through preview→save."""
    client, entry = docs_client
    from cod_doc.services import ai_generate

    def fake(sources, *, cfg, intent="", target_type="module-spec"):
        return ai_generate.DocDraft(
            doc_key="guides/derived",
            title="Derived guide",
            type="guide",
            preamble="Derived from sources.",
            sections=[
                ("Overview", "## Overview body"),
                ("Steps", "1. step\n2. step"),
            ],
            sources=[k for k, _ in sources],
        ), ai_generate.GenerationMeta(
            model="test/m", input_tokens=10, output_tokens=20, duration_ms=5
        )

    monkeypatch.setattr(ai_generate, "generate_doc_from_sources", fake)

    preview = client.post(
        f"/p/{entry.name}/docs/generate",
        data={
            "source": "modules/M1-auth/overview",
            "type": "guide",
            "intent": "make a quickstart",
        },
        headers={"HX-Request": "true"},
    )
    assert preview.status_code == 200
    body = preview.text
    assert "Derived guide" in body
    assert "Overview" in body
    assert 'name="section_heading"' in body
    assert 'name="section_body"' in body

    save = client.post(
        f"/p/{entry.name}/docs/generate/save",
        data={
            "doc_key": "guides/derived",
            "title": "Derived guide",
            "type": "guide",
            "preamble": "Derived from sources.",
            "section_heading": ["Overview", "Steps"],
            "section_body": ["body", "1. one"],
            "source": ["modules/M1-auth/overview"],
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    follow = client.get(f"/p/{entry.name}/docs/guides/derived")
    assert "Derived guide" in follow.text
    assert "Overview" in follow.text


def test_doc_generate_preview_handles_ai_error(docs_client, monkeypatch) -> None:
    client, entry = docs_client
    from cod_doc.services import ai_generate
    from cod_doc.services.ai_text import AIBackendError

    monkeypatch.setattr(
        ai_generate,
        "generate_doc_from_sources",
        lambda *a, **kw: (_ for _ in ()).throw(AIBackendError("budget exceeded")),
    )
    r = client.post(
        f"/p/{entry.name}/docs/generate",
        data={
            "source": "modules/M1-auth/overview",
            "type": "guide",
            "intent": "x",
        },
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "AI error: budget exceeded" in r.text


def test_doc_show_links_panel_renders(docs_client) -> None:
    """Outgoing/incoming sections render even with zero links seeded."""
    client, entry = docs_client
    r = client.get(f"/p/{entry.name}/docs/modules/M1-auth/overview")
    body = r.text
    assert "Outgoing" in body
    assert "Incoming" in body
    assert "No outgoing links yet" in body or "Nobody links here yet" in body


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
    assert "Accept" in r.text and "active" in r.text
    assert f"/p/{entry.name}/docs-accept" in r.text

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
    assert "active" in follow.text


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
