"""WEB-010: tasks list page through TaskService over the embedded sqlite DB."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

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

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_upgrade(db_url: str) -> None:
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        check=True,
        env={"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url},
        capture_output=True,
    )


@pytest.fixture
def tasks_client(tmp_path: Path):
    repo = tmp_path / "demo-tasks"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _alembic_upgrade(f"sqlite:///{db_path}")

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
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks")
    assert r.status_code == 200
    assert "AUTH-001" in r.text
    assert "AUTH-002" in r.text
    assert "AUTH-003" in r.text
    assert "Implement: account deactivation flow" in r.text
    # status badges present (rendered inside each row's status cell)
    assert "badge-pending" in r.text
    assert "badge-in-progress" in r.text
    assert "badge-done" in r.text
    # HTMX inline status form is wired
    assert 'hx-post="/p/demo/tasks/AUTH-001/status"' in r.text
    assert 'hx-target="#task-AUTH-001"' in r.text
    # tab strip: Tasks active
    assert 'class="active" href="/p/demo/tasks"' in r.text
    # count footer
    assert "3 tasks." in r.text


def test_tasks_list_status_filter(tasks_client) -> None:
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=done")
    assert r.status_code == 200
    assert "AUTH-003" in r.text
    assert "AUTH-001" not in r.text
    assert "AUTH-002" not in r.text
    assert "1 task." in r.text


def test_tasks_list_status_filter_in_progress(tasks_client) -> None:
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=in-progress")
    assert r.status_code == 200
    assert "AUTH-002" in r.text
    assert "AUTH-001" not in r.text
    assert "AUTH-003" not in r.text


def test_tasks_list_invalid_status_warns(tasks_client) -> None:
    client, entry = tasks_client
    r = client.get(f"/p/{entry.name}/tasks?status=garbage")
    assert r.status_code == 200
    assert "Неизвестное значение status" in r.text
    # falls back to "all" — every task visible
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
    assert "DB-проект не инициализирован" in r.text


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
