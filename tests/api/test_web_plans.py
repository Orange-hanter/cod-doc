"""WEB-004: plans list + plan detail (Progress / Next batch / Mermaid)."""

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
def plans_client(tmp_path: Path):
    repo = tmp_path / "plans-demo"
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
    plan_id = None
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="payments", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()
        plan_id = plan.row_id

        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Schema",
                slug="schema",
                position=0,
            )
        )
        session.flush()

        # Two pending + one done so progress is meaningful.
        for _ in range(2):
            tasks.create(
                session,
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=section.row_id,
                title=f"Pending {_}",
                type=TaskType.FEATURE,
                priority=Priority.HIGH,
                author="human:dakh",
                id_prefix="PAY",
            )
        d = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Closed task",
            type=TaskType.TEST,
            priority=Priority.LOW,
            author="human:dakh",
            id_prefix="PAY",
        )
        tasks.complete(session, task_id=d.task_id, author="human:dakh")
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry, plan_id


# ── /p/{slug}/plans ──────────────────────────────────────────────────────


def test_plans_list_renders_seed_plan(plans_client) -> None:
    client, entry, _ = plans_client
    r = client.get(f"/p/{entry.name}/plans")
    assert r.status_code == 200
    assert "payments" in r.text
    assert "test-first" in r.text
    # 1 done out of 3 = 33%
    assert 'style="width: 33%"' in r.text
    assert 'class="active" href="/p/demo/plans"' in r.text


def test_plans_list_db_absent_warning(tmp_path: Path) -> None:
    repo = tmp_path / "no-db-plans"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/plans")
    assert r.status_code == 200
    assert "DB-проект не инициализирован" in r.text


# ── /p/{slug}/plans/{plan_id} ────────────────────────────────────────────


def test_plan_show_renders_progress_and_sections(plans_client) -> None:
    client, entry, plan_id = plans_client
    r = client.get(f"/p/{entry.name}/plans/{plan_id}")
    assert r.status_code == 200
    assert "payments" in r.text
    # Section with letter A and title Schema visible
    assert ">A<" in r.text
    assert "Schema" in r.text
    # Progress: 1/3 done
    assert "1 / 3" in r.text or "1/3" in r.text


def test_plan_show_renders_ready_block(plans_client) -> None:
    client, entry, plan_id = plans_client
    r = client.get(f"/p/{entry.name}/plans/{plan_id}")
    assert ">Next batch (ready to start)<" in r.text
    # Pending task is in the ready block
    assert "PAY-001" in r.text
    # complete button wired
    assert 'hx-post="/p/demo/tasks/PAY-001/complete"' in r.text


def test_plan_show_renders_mermaid_export(plans_client) -> None:
    client, entry, plan_id = plans_client
    r = client.get(f"/p/{entry.name}/plans/{plan_id}")
    # Mermaid syntax appears in a <pre> block (interactive renderer deferred).
    assert "graph TD" in r.text
    assert "```mermaid" in r.text


def test_plan_show_404_unknown_plan(plans_client) -> None:
    client, entry, _ = plans_client
    r = client.get(f"/p/{entry.name}/plans/9999")
    assert r.status_code == 404


def test_plan_show_cross_project_404(plans_client, tmp_path: Path) -> None:
    """Plan from project A must not be accessible via project B's URL."""
    client, _entry_a, plan_id = plans_client

    # Create a second project
    repo_b = tmp_path / "other-proj"
    (repo_b / ".cod-doc").mkdir(parents=True)
    db_b = repo_b / ".cod-doc" / "state.db"
    _alembic_upgrade(f"sqlite:///{db_b}")

    import cod_doc.api.deps as deps

    cfg = deps.get_config()
    entry_b = ProjectEntry(name="other", path=str(repo_b))
    cfg.add_project(entry_b)

    engine = make_engine(f"sqlite:///{db_b}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj_b = ProjectRepository(session).add(
            ProjectEntity(slug="other", title="Other", root_path=str(repo_b), config={})
        )
        proj_b.created = now
        proj_b.updated = now
    engine.dispose()

    # Plan from `demo` (id=plan_id) accessed via `other` slug → 404.
    r = client.get(f"/p/{entry_b.name}/plans/{plan_id}")
    assert r.status_code == 404
