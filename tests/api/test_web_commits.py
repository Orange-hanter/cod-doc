"""OBI-011: /p/<slug>/commits page."""

from __future__ import annotations

import subprocess
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
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from pathlib import Path

    pass


def _init_git(repo: Path) -> None:
    def _run(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    _run("init", "-q", "--initial-branch=main")
    _run("config", "user.email", "t@example.com")
    _run("config", "user.name", "Tester")
    _run("config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("a")
    _run("add", "-A")
    _run("commit", "-q", "-m", "feat(WEB-001): initial")
    (repo / "b.txt").write_text("b")
    _run("add", "-A")
    _run("commit", "-q", "-m", "fix(WEB-002): edge")


@pytest.fixture
def commits_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "cmtp"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)
    _init_git(repo)

    entry = ProjectEntry(name="cmtp", path=str(repo))
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
            ProjectEntity(slug="cmtp", title="P", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()
        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="x", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()
        sec = PlanSectionRepository(session).add(
            PlanSection(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
        )
        session.flush()
        for tid in ("WEB-001", "WEB-002"):
            tasks.create(
                session,
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=sec.row_id,
                title=f"Task {tid}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="t",
                task_id=tid,
            )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_commits_page_empty_state(commits_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = commits_client
    r = client.get(f"/p/{entry.name}/commits")
    assert r.status_code == 200
    assert "No linked commits" in r.text
    assert "Re-scan git log" in r.text


def test_commits_import_then_page_shows_rows(commits_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = commits_client
    r = client.post(f"/p/{entry.name}/commits/import", follow_redirects=False)
    assert r.status_code == 303
    # Now the table is populated.
    page = client.get(f"/p/{entry.name}/commits")
    assert page.status_code == 200
    assert "WEB-001" in page.text
    assert "WEB-002" in page.text
    # Each appears as a task link.
    assert f"/p/{entry.name}/tasks/WEB-001" in page.text


def test_commits_tab_in_project_navigation(commits_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = commits_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert f'href="/p/{entry.name}/commits"' in r.text
