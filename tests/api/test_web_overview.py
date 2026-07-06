"""WEB-014: overview aggregator on `/p/{slug}` (Ready / Plan progress / Recent).

Verifies:
- Ready-to-start block renders pending tasks (top-N) with a complete ✓ button.
- POST /p/{slug}/tasks/{id}/complete works under HTMX (row swap to done) and
  under form-post (303 redirect to overview, optional cookie-flash).
- Plan progress mini-bars render done/total per plan.
- Recent revisions block lists author + entity_kind#id (newest first).
- Empty / DB-not-initialised project doesn't crash; placeholder text shown.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    Plan,
    PlanSection,
    Priority,
    TaskType,
)
from cod_doc.domain.entities import (
    Project as ProjectEntity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def overview_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "ov-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
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
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="ov-bootstrap", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()

        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Section A",
                slug="section-a",
                position=0,
            )
        )
        session.flush()

        # 3 pending (ready) + 1 done so progress bar isn't 0%.
        for _ in range(3):
            tasks.create(
                session,
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=section.row_id,
                title=f"Pending task {_}",
                type=TaskType.FEATURE,
                priority=Priority.HIGH,
                author="human:dakh",
                id_prefix="OVR",
            )
        done_t = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Already done",
            type=TaskType.TEST,
            priority=Priority.LOW,
            author="human:dakh",
            id_prefix="OVR",
        )
        tasks.complete(session, task_id=done_t.task_id, author="human:dakh")
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


# ── Read-side rendering ──────────────────────────────────────────────────


def test_overview_ready_block_visible(overview_client) -> None:
    client, entry = overview_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert ">Ready to start<" in r.text
    # Each ready row is identifiable by task id.
    assert 'id="task-OVR-001"' in r.text
    # Complete button is wired with HTMX
    assert 'hx-post="/p/demo/tasks/OVR-001/complete"' in r.text


def test_overview_plan_progress_visible(overview_client) -> None:
    client, entry = overview_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert ">Plan progress<" in r.text
    # The seeded scope name + total/done columns.
    assert "ov-bootstrap" in r.text
    # progress-bar with width style — 1 done out of 4 = 25%
    assert "progress-fill" in r.text
    assert 'style="width: 25%"' in r.text


def test_overview_recent_revisions_visible(overview_client) -> None:
    client, entry = overview_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert ">Recent revisions<" in r.text
    # Author tag of the seeded revisions.
    assert "human:dakh" in r.text
    # task entities show up as "task#NN"
    assert "task#" in r.text


def test_overview_empty_db_renders_placeholder(tmp_path: Path) -> None:
    """When `.cod-doc/state.db` is missing, overview shows a graceful note."""
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
        r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    # No agg blocks, no crash; instead — the empty-DB banner with init button.
    assert "Project database not initialized" in r.text
    assert 'action="/p/bare/init"' in r.text
    assert "Initialize DB" in r.text
    assert "Ready to start" not in r.text


# ── POST /tasks/{id}/complete ─────────────────────────────────────────────


def test_complete_post_htmx_swaps_done_row(overview_client) -> None:
    client, entry = overview_client
    r = client.post(
        f"/p/{entry.name}/tasks/OVR-001/complete",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert 'id="task-OVR-001"' in r.text
    assert "badge-done" in r.text
    # Persisted: GET tasks?status=done now includes OVR-001.
    r2 = client.get(f"/p/{entry.name}/tasks?status=done")
    assert "OVR-001" in r2.text


def test_complete_post_form_redirects_to_overview(overview_client) -> None:
    client, entry = overview_client
    r = client.post(
        f"/p/{entry.name}/tasks/OVR-002/complete",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{entry.name}"


def test_complete_post_already_done_returns_info_alert(overview_client) -> None:
    client, entry = overview_client
    # OVR-004 is already done (seeded).
    r = client.post(
        f"/p/{entry.name}/tasks/OVR-004/complete",
        headers={"HX-Request": "true"},
    )
    # Service raises TaskAlreadyDoneError → row + info alert (200).
    assert r.status_code == 200
    assert "alert-info" in r.text
    assert "already done" in r.text


def test_complete_post_unknown_task_404(overview_client) -> None:
    client, entry = overview_client
    r = client.post(
        f"/p/{entry.name}/tasks/UNKNOWN-999/complete",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404
    assert "alert-error" in r.text  # via WebError handler
