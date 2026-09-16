"""ADO-110: Agent Console Stop/Resume pauses this project, not the global daemon."""

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

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def console_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "console-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(
        api_key="sk-test",
        model="test/model",
        base_url="https://x",
        agent_enabled=False,
    )
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
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_run_console_stop_button_is_per_project(console_client) -> None:
    client, entry = console_client
    r = client.get(f"/p/{entry.name}/run")
    assert r.status_code == 200
    assert 'id="agent-console-stop"' in r.text
    assert f'hx-post="/api/projects/{entry.name}/daemon/stop"' in r.text
    assert 'hx-post="/api/daemon/stop"' not in r.text


def test_project_daemon_stop_then_resume(console_client, monkeypatch: pytest.MonkeyPatch) -> None:
    client, entry = console_client
    called: list[str] = []
    monkeypatch.setattr(
        "cod_doc.api.routes.stop_daemon",
        lambda: called.append("stop") or False,
    )

    stopped = client.post(f"/api/projects/{entry.name}/daemon/stop")
    assert stopped.status_code == 200, stopped.text
    body = stopped.json()
    assert body["daemon_enabled"] is False
    assert body["scope"] == "project"
    assert called == []

    page = client.get(f"/p/{entry.name}/run")
    assert page.status_code == 200
    assert 'id="agent-console-resume"' in page.text
    assert 'id="agent-console-stop"' not in page.text

    resumed = client.post(f"/api/projects/{entry.name}/daemon/start")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["daemon_enabled"] is True

    page2 = client.get(f"/p/{entry.name}/run")
    assert page2.status_code == 200
    assert 'id="agent-console-stop"' in page2.text
