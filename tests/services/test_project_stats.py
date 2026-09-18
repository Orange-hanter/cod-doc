"""ADO-076: project list / API stats come from the DB, not tasks.yaml."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner
from fastapi.testclient import TestClient

from cod_doc.cli.cmd_project import project as project_group
from cod_doc.config import Config
from cod_doc.core.project import Project, Task, TaskStatus
from cod_doc.domain.entities import Plan, PlanSection, Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import AgentRunModel
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import project_stats, task_service

if TYPE_CHECKING:
    from pathlib import Path


def test_canonical_by_status_folds_legacy_aliases() -> None:
    folded = project_stats.canonical_by_status(
        {"pending": 2, "in-progress": 1, "done": 3, "failed": 9}
    )
    assert folded["todo"] == 2
    assert folded["in_progress"] == 1
    assert folded["done"] == 3
    assert "failed" not in folded
    assert "pending" not in folded


def _seed_db_tasks(entry, *, n_todo: int, n_done: int) -> None:
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            project = ProjectRepository(session).get_by_slug(entry.name)
            assert project is not None and project.row_id is not None
            now = datetime.now(UTC)
            plan = PlanRepository(session).add(
                Plan(
                    project_id=project.row_id,
                    scope="ado076-stats",
                    principle="test-first",
                    created=now,
                    last_updated=now,
                )
            )
            session.flush()
            section = PlanSectionRepository(session).add(
                PlanSection(
                    plan_id=plan.row_id,
                    letter="A",
                    title="Stats",
                    slug="stats",
                    position=0,
                )
            )
            session.flush()
            for i in range(n_todo + n_done):
                task = task_service.create(
                    session,
                    project_id=project.row_id,
                    plan_id=plan.row_id,
                    section_id=section.row_id,
                    title=f"DB task {i}",
                    type=TaskType.FEATURE,
                    priority=Priority.MEDIUM,
                    author="human:test",
                    id_prefix="ST",
                )
                if i < n_done:
                    task_service.complete(session, task_id=task.task_id, author="human:test")
    finally:
        engine.dispose()


def _write_lying_yaml(entry) -> None:
    proj = Project(entry)
    for i in range(24):
        proj.add_task(Task(title=f"yaml done {i}", status=TaskStatus.DONE, priority=5))
    for i in range(7):
        proj.add_task(Task(title=f"yaml failed {i}", status=TaskStatus.FAILED, priority=5))
    proj.add_task(Task(title="yaml pending", status=TaskStatus.PENDING, priority=5))
    proj.set_status("running")


def test_stats_ignore_lying_yaml_when_db_has_tasks(tmp_path: Path) -> None:
    repo = tmp_path / "stats-demo"
    repo.mkdir()
    cfg = Config(
        api_key="sk-test",
        model="test/model",
        base_url="https://x",
        agent_enabled=False,
    )
    result = CliRunner().invoke(
        project_group,
        ["add", str(repo), "--name", "stats-demo"],
        obj={"config": cfg},
    )
    assert result.exit_code == 0, result.output
    entry = cfg.get_project("stats-demo")
    assert entry is not None

    _write_lying_yaml(entry)
    _seed_db_tasks(entry, n_todo=2, n_done=1)

    stats = project_stats.stats_for_entry(entry)
    assert stats["source"] == "db"
    assert stats["by_status"]["todo"] == 2
    assert stats["by_status"]["done"] == 1
    assert stats["by_status"]["done"] != 24
    assert stats["status"] == "idle"
    assert stats["total"] == 3

    listed = CliRunner().invoke(project_group, ["list"], obj={"config": cfg})
    assert listed.exit_code == 0, listed.output
    assert "done 24" not in listed.output
    assert "todo 2" in listed.output
    assert "done 1" in listed.output
    assert "idle" in listed.output

    status = CliRunner().invoke(
        project_group, ["status", "stats-demo", "--json"], obj={"config": cfg}
    )
    assert status.exit_code == 0, status.output

    payload = json.loads(status.output)
    assert payload["stats"]["source"] == "db"
    assert payload["stats"]["by_status"]["todo"] == 2
    assert payload["stats"]["status"] == "idle"

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        rows = resp.json()
        match = next(row for row in rows if row["name"] == "stats-demo")
        assert match["stats"]["source"] == "db"
        assert match["stats"]["by_status"]["todo"] == 2
        assert match["stats"]["by_status"]["done"] == 1
        assert match["stats"]["status"] == "idle"

        one = client.get("/api/projects/stats-demo")
        assert one.status_code == 200
        assert one.json()["stats"]["total"] == 3

    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            project = ProjectRepository(session).get_by_slug(entry.name)
            assert project is not None and project.row_id is not None
            session.add(
                AgentRunModel(
                    run_id="ado076-running",
                    project_id=project.row_id,
                    status="running",
                )
            )
    finally:
        engine.dispose()

    running = project_stats.stats_for_entry(entry)
    assert running["status"] == "running"
    assert running["source"] == "db"
