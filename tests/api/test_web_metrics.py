"""OBI-002: metrics dashboard web page."""

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


@pytest.fixture
def metrics_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "metp"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="metp", path=str(repo))
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
            ProjectEntity(slug="metp", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now; proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="x", principle="test-first")
        )
        plan.created = now; plan.last_updated = now
        session.flush()

        sec = PlanSectionRepository(session).add(
            PlanSection(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
        )
        session.flush()

        # Two completed tasks of different types — gives non-empty summary.
        for i, ttype in enumerate([TaskType.FEATURE, TaskType.BUG]):
            t = tasks.create(
                session,
                project_id=proj.row_id, plan_id=plan.row_id, section_id=sec.row_id,
                title=f"Task {i}", type=ttype, priority=Priority.MEDIUM,
                author="t", id_prefix="MET",
            )
            tasks.complete(session, task_id=t.task_id, author="t")
    engine.dispose()

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_metrics_page_renders_summary(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = metrics_client
    r = client.get(f"/p/{entry.name}/metrics")
    assert r.status_code == 200
    # Overall section
    assert "Completed" in r.text
    assert ">2<" in r.text or "completed: 2" in r.text.lower() or "2</div>" in r.text
    # By type table with feature + bug rows
    assert "feature" in r.text
    assert "bug" in r.text


def test_metrics_page_renders_sparkline(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = metrics_client
    r = client.get(f"/p/{entry.name}/metrics?days=14")
    assert r.status_code == 200
    assert "sparkline" in r.text
    # 14 bar elements expected. Count opening tags only (the CSS block also
    # mentions the class name).
    assert r.text.count('<div class="sparkline-bar-wrap"') == 14


def test_metrics_page_no_data_state(metrics_client, tmp_path: Path, migrate_db) -> None:  # type: ignore[no-untyped-def]
    """Fresh project with zero completions shows the empty-state hint."""
    repo = tmp_path / "empty"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="empty-metp", path=str(repo))
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
            ProjectEntity(slug="empty-metp", title="X", root_path=str(repo), config={})
        )
        proj.created = now; proj.updated = now
    engine.dispose()

    from cod_doc.api.server import app
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/p/empty-metp/metrics")
    assert r.status_code == 200
    assert "No completed tasks" in r.text


def test_metrics_tab_in_project_navigation(metrics_client) -> None:  # type: ignore[no-untyped-def]
    client, entry = metrics_client
    r = client.get(f"/p/{entry.name}")
    assert r.status_code == 200
    assert f'href="/p/{entry.name}/metrics"' in r.text
