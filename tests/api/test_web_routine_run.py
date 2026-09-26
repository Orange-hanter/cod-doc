"""Web: ручной запуск рутины отдаёт JSON, а не 500.

``routine_run`` клал ``run.started_at`` (``datetime``) в ``JSONResponse`` как
есть; ``json.dumps`` падал ``TypeError`` уже после успешного прогона, и кнопка
Run на /routines всегда отвечала 500.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import routine_service

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@pytest.fixture
def client_with_routine(
    tmp_path: Path, migrate_db: Callable[[Path], None]
) -> Iterator[tuple[TestClient, str]]:
    repo = tmp_path / "rr-demo"
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
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        session.flush()
        assert proj.row_id is not None
        routine_service.create(
            session, proj.row_id, name="stale_daily", check_name="task_stale", cron="0 9 * * *"
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry.name


def test_run_returns_json_with_iso_started_at(
    client_with_routine: tuple[TestClient, str],
) -> None:
    client, slug = client_with_routine
    resp = client.post(f"/p/{slug}/routines/stale_daily/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "stale_daily"
    datetime.fromisoformat(body["started_at"])


def test_run_unknown_routine_is_404(client_with_routine: tuple[TestClient, str]) -> None:
    client, slug = client_with_routine
    assert client.post(f"/p/{slug}/routines/nope/run").status_code == 404
