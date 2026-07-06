"""WEB-010: tasks list page through TaskService over the embedded sqlite DB."""

from __future__ import annotations

import re
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
    TaskStatus,
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
def tasks_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "demo-tasks"
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
            Plan(project_id=proj.row_id, scope="auth-bootstrap", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()

        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Test Coverage",
                slug="test-coverage",
                position=0,
            )
        )
        session.flush()

        # Three tasks: one pending, one in-progress, one done.
        tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Test: getMyAgency returns profile",
            type=TaskType.TEST,
            priority=Priority.HIGH,
            author="human:dakh",
            id_prefix="AUTH",
        )
        t2 = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Implement: account deactivation flow",
            type=TaskType.FEATURE,
            priority=Priority.CRITICAL,
            author="human:dakh",
            id_prefix="AUTH",
        )
        t3 = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Refactor: token rotation",
            type=TaskType.REFACTOR,
            priority=Priority.LOW,
            author="human:dakh",
            id_prefix="AUTH",
        )
        tasks.update_status(
            session,
            task_id=t2.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:dakh",
        )
        tasks.complete(session, task_id=t3.task_id, author="human:dakh")
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_tasks_list_renders_all(tasks_client) -> None:
    """Default landing page shows every task on the kanban board."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    # All three tasks present, each as a kanban card with anchor id.
    assert 'id="card-AUTH-001"' in r.text
    assert 'id="card-AUTH-002"' in r.text
    assert 'id="card-AUTH-003"' in r.text
    assert "Implement: account deactivation flow" in r.text
    # Kanban columns rendered (status = column).
    assert "kanban-col-todo" in r.text
    assert "kanban-col-in_progress" in r.text
    assert "kanban-col-done" in r.text
    # Card carries status + priority classes used by CSS.
    assert "status-pending" in r.text
    assert "status-in-progress" in r.text
    assert "status-done" in r.text
    assert re.search(
        r'href="/p/demo/tasks"[^>]*\bactive\b|class="[^"]*\bactive\b[^"]*"[^>]*href="/p/demo/tasks"',
        r.text,
    )
    # Live refresh region for WebSocket kanban sync
    assert 'id="tasks-live-region"' in r.text
    assert "frag/tasks/board" in r.text
    # count footer
    assert "3 task(s) shown." in r.text


def test_tasks_list_done_column_collapsed_by_default(tasks_client) -> None:
    """Done/Cancelled lanes are pre-collapsed so the eye lands on open work."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200

    # Find the Done column markup and confirm it has no `open` attribute.
    # Other columns with tasks should be open.
    import re

    done_block = re.search(r'<details class="kanban-col kanban-col-done[^"]*"([^>]*)>', r.text)
    assert done_block is not None, "Done column must render"
    assert "open" not in done_block.group(1), "Done column should be collapsed by default"

    in_progress_block = re.search(
        r'<details class="kanban-col kanban-col-in_progress[^"]*"([^>]*)>', r.text
    )
    assert in_progress_block is not None
    assert "open" in in_progress_block.group(1), "Non-empty active lane should start open"


def test_tasks_list_status_highlights_column(tasks_client) -> None:
    """`?status=done` highlights the Done column but keeps every task visible."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=done")
    assert r.status_code == 200
    # Highlight applied to the Done column.
    assert "kanban-col-done kanban-col-highlighted" in r.text
    # Other tasks are still on the board (kanban shows everything).
    assert 'id="card-AUTH-001"' in r.text
    assert 'id="card-AUTH-003"' in r.text


def test_tasks_list_status_in_progress_highlights_column(tasks_client) -> None:
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=in-progress")
    assert r.status_code == 200
    assert "kanban-col-in_progress kanban-col-highlighted" in r.text
    assert 'id="card-AUTH-002"' in r.text


def test_tasks_list_plan_filter(tasks_client) -> None:
    """`?plan=<scope>` restricts the board to one plan; unknown plan → empty."""
    client, entry = tasks_client
    # All three demo tasks belong to plan `auth-bootstrap` — they remain visible.
    r = client.get(f"/p/{entry.name}/tasks?plan=auth-bootstrap")
    assert r.status_code == 200
    assert "plan-chip-active" in r.text
    assert 'id="card-AUTH-001"' in r.text
    # Unknown plan → no tasks shown.
    r2 = client.get(f"/p/{entry.name}/tasks?plan=does-not-exist")
    assert r2.status_code == 200
    assert "AUTH-001" not in r2.text


def test_tasks_list_chains_view(tasks_client) -> None:
    """`?view=chains` renders the dependency-graph layout per plan."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?view=chains")
    assert r.status_code == 200
    # View toggle present and chains tab active.
    assert "tasks-view-toggle" in r.text
    assert "tasks-view-btn-active" in r.text
    # Chain section for the demo plan rendered.
    assert 'id="chain-auth-bootstrap"' in r.text
    assert "chain-lane" in r.text
    # All tasks are nodes on the chain board.
    assert 'id="card-AUTH-001"' in r.text
    assert 'id="card-AUTH-002"' in r.text
    assert 'id="card-AUTH-003"' in r.text
    # With no dependency edges set, the "no edges" notice is shown.
    assert "нет зависимостей" in r.text


def test_tasks_list_invalid_status_warns(tasks_client) -> None:
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=garbage&view=all")
    assert r.status_code == 200
    assert "Unknown status value" in r.text
    # falls back to the requested view — `view=all` shows every task.
    assert "AUTH-001" in r.text
    assert "AUTH-002" in r.text
    assert "AUTH-003" in r.text


def test_tasks_list_warns_when_db_absent(tmp_path: Path) -> None:
    repo = tmp_path / "no-db-tasks"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    assert "DB project not initialized" in r.text


def test_tasks_list_404_unknown_project(tasks_client) -> None:
    client, _ = tasks_client
    r = client.get("/p/nope/tasks")
    assert r.status_code == 404


# ── WEB-011: HTMX inline status update ──────────────────────────────────────


def test_status_post_htmx_returns_row_fragment(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "in-progress"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # Row id present, swap-friendly
    assert 'id="task-AUTH-001"' in r.text
    # New status reflected in badge + selected option
    assert "badge-in-progress" in r.text
    assert '<option value="in-progress" selected>' in r.text
    # No row-error span when success
    assert "row-error" not in r.text


def test_status_post_form_redirects_back_to_list(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "in-progress"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{entry.name}/tasks"


def test_status_post_no_op_same_status(tasks_client) -> None:
    """Posting the same status as current → service no-op, returns row unchanged."""
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "pending"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "badge-pending" in r.text


def test_status_post_unknown_task_404(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/NO-999/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_status_post_invalid_status_value_400(tasks_client) -> None:
    client, entry = tasks_client
    r = client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "garbage"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 400


def test_status_post_404_unknown_project(tasks_client) -> None:
    client, _ = tasks_client
    r = client.post(
        "/p/nope/tasks/AUTH-001/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_status_post_404_db_absent(tmp_path: Path) -> None:
    repo = tmp_path / "no-db-post"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))

    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.post(
            f"/p/{entry.name}/tasks/X-001/status",
            data={"status": "done"},
            headers={"HX-Request": "true"},
        )
    assert r.status_code == 404


def test_status_post_persists_change(tasks_client) -> None:
    """After a HTMX post, GET /tasks shows the updated status."""
    client, entry = tasks_client
    client.post(
        f"/p/{entry.name}/tasks/AUTH-001/status",
        data={"status": "done"},
        headers={"HX-Request": "true"},
    )
    r = client.get(f"/p/{entry.name}/tasks?status=done")
    assert r.status_code == 200
    assert "AUTH-001" in r.text  # now also done
    assert "AUTH-003" in r.text  # was already done


def test_tasks_board_fragment(tasks_client) -> None:
    """HTMX fragment for live kanban refresh returns stats + board."""
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/frag/tasks/board")
    assert r.status_code == 200
    assert 'id="tasks-live-region"' in r.text
    assert 'id="card-AUTH-001"' in r.text
    assert "tasks-stats" in r.text
