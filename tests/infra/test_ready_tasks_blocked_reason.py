"""ADO-225: задача с внешним блокером (`blocked_reason`) не готова к старту.

`set_blocker` статус не трогает — из ready-множества задачу выводит фильтр
`blocked_reason IS NULL` во view `ready_tasks` (миграция 0041). Ожидаемые
значения — литералы и прямые SELECT по view, а не вывод другого сервиса.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import plan_service, task_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_PREVIOUS = "0040_document_status_resolved_done"
_AUTHOR = "human:test"


@pytest.fixture
def engine_with_schema(tmp_path: Path) -> Iterator[Engine]:
    """Свежая SQLite со схемой на head — как в test_ready_tasks_cancelled."""
    db_url = f"sqlite:///{tmp_path / 'ready.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    project = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    project.created = now
    project.updated = now
    session.add(project)
    session.flush()
    plan = PlanModel(
        project_id=project.row_id,
        scope="p-plan",
        principle="test-first",
        created=now,
        last_updated=now,
    )
    session.add(plan)
    session.flush()
    section = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(section)
    session.flush()
    return project.row_id, plan.row_id, section.row_id


def _make(
    session: Session,
    ids: tuple[int, int, int],
    task_id: str,
    *,
    blocked_by: list[str] | None = None,
    blocked_reason: str | None = None,
) -> None:
    project_id, plan_id, section_id = ids
    task_service.create(
        session,
        project_id=project_id,
        plan_id=plan_id,
        section_id=section_id,
        task_id=task_id,
        title=f"Implement: {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=_AUTHOR,
        blocked_by=blocked_by,
        blocked_reason=blocked_reason,
    )


def _ready(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {str(r[0]) for r in conn.execute(text("SELECT task_id FROM ready_tasks"))}


def _status(engine: Engine, task_id: str) -> str:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM task WHERE task_id = :t"), {"t": task_id}
        ).fetchone()
    assert row is not None
    return str(row[0])


def _two_todo_tasks(engine: Engine) -> int:
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        ids = _seed(session)
        _make(session, ids, "TA-001")
        _make(session, ids, "TA-002")
    return ids[0]


def test_set_blocker_removes_task_from_view(engine_with_schema: Engine) -> None:
    _two_todo_tasks(engine_with_schema)
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        task_service.set_blocker(session, task_id="TA-001", reason="ждём API", author=_AUTHOR)

    assert _ready(engine_with_schema) == {"TA-002"}
    assert _status(engine_with_schema, "TA-001") == "todo"


def test_clear_blocker_returns_task_to_view(engine_with_schema: Engine) -> None:
    _two_todo_tasks(engine_with_schema)
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        task_service.set_blocker(session, task_id="TA-001", reason="ждём API", author=_AUTHOR)
    with transactional(factory) as session:
        task_service.clear_blocker(session, task_id="TA-001", author=_AUTHOR)

    assert _ready(engine_with_schema) == {"TA-001", "TA-002"}


def test_ready_for_project_respects_blocker(engine_with_schema: Engine) -> None:
    project_id = _two_todo_tasks(engine_with_schema)
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        task_service.set_blocker(session, task_id="TA-001", reason="ждём API", author=_AUTHOR)
    with transactional(factory) as session:
        blocked = plan_service.ready_for_project(session, project_id, local_only=False)
        assert [t.task_id for t in blocked] == ["TA-002"]

    with transactional(factory) as session:
        task_service.clear_blocker(session, task_id="TA-001", author=_AUTHOR)
    with transactional(factory) as session:
        cleared = plan_service.ready_for_project(session, project_id, local_only=False)
        assert {t.task_id for t in cleared} == {"TA-001", "TA-002"}


def test_chain_layout_ready_ids_respect_blocker(engine_with_schema: Engine) -> None:
    # Граф плана обязан совпадать с view: иначе страница графа показывает
    # «готовой» задачу, которую plan_ready не отдаёт.
    _two_todo_tasks(engine_with_schema)
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        task_service.set_blocker(session, task_id="TA-001", reason="ждём API", author=_AUTHOR)
    with transactional(factory) as session:
        plan_id = session.execute(text("SELECT row_id FROM plan")).scalar_one()
        layout = plan_service.chain_layout(session, plan_id)
    assert layout["ready_ids"] == {"TA-002"}

    with transactional(factory) as session:
        task_service.clear_blocker(session, task_id="TA-001", author=_AUTHOR)
    with transactional(factory) as session:
        layout = plan_service.chain_layout(session, plan_id)
    assert layout["ready_ids"] == {"TA-001", "TA-002"}


def test_clear_blocker_keeps_dependency_rule(engine_with_schema: Engine) -> None:
    """Снятый внешний блокер не отменяет открытое ребро `blocks`."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        ids = _seed(session)
        _make(session, ids, "TA-004")
        _make(session, ids, "TA-003", blocked_by=["TA-004"], blocked_reason="ждём API")
    with transactional(factory) as session:
        task_service.clear_blocker(session, task_id="TA-003", author=_AUTHOR)

    assert _ready(engine_with_schema) == {"TA-004"}


def test_live_view_sql_filters_blocked_reason(engine_with_schema: Engine) -> None:
    with engine_with_schema.connect() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='view' AND name='ready_tasks'")
        ).fetchone()
    assert row is not None, "view ready_tasks не создан"
    assert "blocked_reason is null" in str(row[0]).lower()


def _seed_raw(db_path: Path) -> None:
    """На ревизии 0040: TA-001 с внешним блокером, TA-002 без — прямым SQL."""
    now = datetime.now(UTC).isoformat(sep=" ")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO project (row_id, slug, title, root_path, created, updated, config_json)"
            " VALUES (1, 'p', 'P', '/tmp/p', ?, ?, '{}')",
            (now, now),
        )
        conn.execute(
            "INSERT INTO plan (row_id, project_id, scope, created, last_updated)"
            " VALUES (1, 1, 'p-plan', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO plan_section (row_id, plan_id, letter, title, slug, position)"
            " VALUES (1, 1, 'A', 'A', 'A', 0)"
        )
        for row_id, task_id, reason in ((1, "TA-001", "ждём API"), (2, "TA-002", None)):
            conn.execute(
                "INSERT INTO task (row_id, project_id, task_id, plan_id, section_id, title,"
                " status, type, priority, created, last_updated, blocked_reason)"
                " VALUES (?, 1, ?, 1, 1, ?, 'todo', 'feature', 'medium', ?, ?, ?)",
                (row_id, task_id, task_id, now, now, reason),
            )
        conn.commit()
    finally:
        conn.close()


def _ready_raw(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {str(r[0]) for r in conn.execute("SELECT task_id FROM ready_tasks")}
    finally:
        conn.close()


def test_migration_hides_preexisting_blocked_task(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed_raw(db_path)
    assert _ready_raw(db_path) == {"TA-001", "TA-002"}

    run_alembic("upgrade", "head", db_url=db_url)
    assert _ready_raw(db_path) == {"TA-002"}

    run_alembic("downgrade", "-1", db_url=db_url)
    assert _ready_raw(db_path) == {"TA-001", "TA-002"}
