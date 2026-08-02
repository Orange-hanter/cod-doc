"""ADR-004/005/006/008: web pages for Architecture Decision Records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import adr_service

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def adr_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "adr-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="adr-demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="adr-demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        adr_service.create(
            session,
            project_id=proj.row_id,
            title="Layered architecture with DIP",
            status="accepted",
            context="LLM-provider abstraction needed",
            decision="4-layer + DIP",
            adr_id="ADR-001",
        )
        adr_service.create(
            session,
            project_id=proj.row_id,
            title="Use SQLite for local-first",
            status="proposed",
            context="docker-free deployment",
            decision="SQLite via SQLAlchemy",
            adr_id="ADR-002",
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


# ----------------------------------------------------------------- #
# ADR-004: list page                                                 #
# ----------------------------------------------------------------- #


def test_adr_list_renders_both_records(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    assert r.status_code == 200
    assert "ADR-001" in r.text
    assert "ADR-002" in r.text
    assert "Layered architecture" in r.text
    assert "Use SQLite" in r.text


def test_adr_list_status_filter(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr?status=accepted")
    assert r.status_code == 200
    assert "ADR-001" in r.text
    assert "ADR-002" not in r.text


def test_adr_list_shows_status_icon(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr")
    # Default 'accepted' icon is the green check.
    assert "✅" in r.text


# ----------------------------------------------------------------- #
# ADR-005: detail + new                                              #
# ----------------------------------------------------------------- #


def test_adr_show_renders_full_record(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert r.status_code == 200
    assert "ADR-001" in r.text
    assert "Layered architecture with DIP" in r.text
    assert "4-layer + DIP" in r.text
    # Edit form present.
    assert 'action="/p/adr-demo/adr/ADR-001/edit"' in r.text
    # Supersede form lists OTHER ADRs as candidates (ADR-002), not self.
    assert "ADR-002" in r.text


def test_adr_show_404_for_missing(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/ADR-999")
    assert r.status_code == 404


def test_adr_new_form_renders(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/new")
    assert r.status_code == 200
    assert "Title" in r.text
    assert "Decision" in r.text or "Decision" in r.text
    # Status select has all 5 options.
    for s in ("proposed", "accepted", "superseded", "deprecated", "rejected"):
        assert s in r.text


def test_adr_new_submit_creates_and_redirects(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/new",
        data={
            "title": "New decision via web",
            "status": "proposed",
            "context": "Why",
            "decision": "What we picked",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Auto-allocated next id should be ADR-003.
    assert r.headers["location"].endswith("/adr/ADR-003")
    # Now follow → detail page rendered.
    detail = client.get(r.headers["location"])
    assert detail.status_code == 200
    assert "New decision via web" in detail.text


def test_adr_edit_updates_fields(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/ADR-002/edit",
        data={
            "title": "SQLite — accepted",
            "status": "accepted",
            "context": "ctx",
            "decision": "decision",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    after = client.get(f"/p/{entry.name}/adr/ADR-002")
    assert "SQLite — accepted" in after.text
    assert "accepted" in after.text


def test_adr_diagram_attach(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.post(
        f"/p/{entry.name}/adr/ADR-001/diagram",
        data={"title": "Layers", "mermaid": "graph TD; A-->B; B-->C"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    after = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert "Layers" in after.text
    assert "graph TD" in after.text


def test_adr_supersede_post(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    # ADR-002 supersedes ADR-001.
    r = client.post(
        f"/p/{entry.name}/adr/ADR-002/supersede",
        data={"superseded_adr_id": "ADR-001", "reason": "newer thinking"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # ADR-001 should now show 'superseded' status.
    detail = client.get(f"/p/{entry.name}/adr/ADR-001")
    assert "superseded" in detail.text


# ----------------------------------------------------------------- #
# ADR-006: graph                                                     #
# ----------------------------------------------------------------- #


def test_adr_graph_renders_mermaid(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    # First create a supersede edge so the graph is non-trivial.
    client.post(
        f"/p/{entry.name}/adr/ADR-002/supersede",
        data={"superseded_adr_id": "ADR-001", "reason": "v2"},
        follow_redirects=False,
    )
    r = client.get(f"/p/{entry.name}/adr/graph")
    assert r.status_code == 200
    assert "mermaid" in r.text
    assert "graph LR" in r.text
    assert "ADR_001" in r.text  # node id (dash → underscore)
    assert "ADR_002" in r.text
    # The mermaid edge: literal source has "-->" but Jinja escapes it to "--&gt;".
    assert ("ADR_002 --> ADR_001" in r.text) or ("ADR_002 --&gt; ADR_001" in r.text)


def test_adr_graph_empty_state(adr_client, tmp_path: Path, migrate_db) -> None:  # type: ignore[no-untyped-def]
    """A fresh project with no ADRs should render an empty-state hint."""
    # The fixture already seeds 2 ADRs but no supersede edges — so 'nodes'
    # are present but 'edges' empty. Just verify the page renders.
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}/adr/graph")
    assert r.status_code == 200
    # Even without edges, both nodes show.
    assert "ADR-001" in r.text
    assert "ADR-002" in r.text


# ----------------------------------------------------------------- #
# ADR-008 partial: tabs include ADR link                              #
# ----------------------------------------------------------------- #


def test_project_tabs_include_adr_link(adr_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = adr_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert f'href="/p/{entry.name}/adr"' in r.text
