"""WUI-012: лента активности — люди и агенты строками, рутины сводкой дня.

На живом Orakul 95% событий недели — рутины (`link.verified`, `routine.fired`).
Лента по умолчанию обязана показывать работу людей и агентов, а шум —
одной строкой-сводкой дня, не тысячами строк.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.services import activity_service
from cod_doc.services.project_lookup import require_project_id

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def feed_client(tmp_path: Path, migrate_db):  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    from cod_doc.domain.entities import Project as ProjectEntity
    from cod_doc.infra.repositories import ProjectRepository

    repo = tmp_path / "feed-demo"
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
    with transactional(make_session_factory(engine)) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()
        pid = require_project_id(session, "demo")
        activity_service.emit(
            session,
            project_id=pid,
            actor_kind="human",
            actor_id="human:dakh",
            kind="task.completed",
            scope_kind="task",
            scope_id="PAY-001",
            summary="закрыл задачу",
        )
        for _ in range(5):
            activity_service.emit(
                session,
                project_id=pid,
                actor_kind="routine",
                actor_id="routine:link_integrity",
                kind="link.verified",
                scope_kind="document",
                scope_id="MASTER",
            )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def test_default_feed_shows_people_and_folds_routines(feed_client) -> None:  # type: ignore[no-untyped-def]
    r = feed_client.get("/p/demo/activity")
    assert r.status_code == 200
    assert 'data-event-kind="task.completed"' in r.text
    assert 'data-event-kind="link.verified"' not in r.text
    # пять событий рутины — одна строка-сводка дня
    assert "link.verified ×5" in r.text
    assert 'Люди и агенты <span class="l-filter-count">1</span>' in r.text
    assert 'Рутины <span class="l-filter-count">5</span>' in r.text
    assert 'href="/p/demo/tasks/PAY-001"' in r.text


def test_routines_view_lists_noise_rows(feed_client) -> None:  # type: ignore[no-untyped-def]
    r = feed_client.get("/p/demo/activity?who=routines")
    assert r.status_code == 200
    assert r.text.count('data-event-kind="link.verified"') == 5
    assert 'data-event-kind="task.completed"' not in r.text
    assert "link.verified ×5" not in r.text  # сводка не дублирует строки


def test_unknown_who_falls_back_to_people(feed_client) -> None:  # type: ignore[no-untyped-def]
    r = feed_client.get("/p/demo/activity?who=bogus")
    assert r.status_code == 200
    assert 'data-event-kind="task.completed"' in r.text
