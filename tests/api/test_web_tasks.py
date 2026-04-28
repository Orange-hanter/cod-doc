"""WEB-010: tasks list page through TaskService over the embedded sqlite DB."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    Plan,
    PlanSection,
    Priority,
    Project as ProjectEntity,
    TaskStatus,
    TaskType,
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
        now = datetime.now(timezone.utc)
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
    # status badges present
    assert "badge-pending" in r.text
    assert "badge-in-progress" in r.text
    assert "badge-done" in r.text
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
