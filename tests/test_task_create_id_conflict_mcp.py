"""AFT-011 (RFC 27 F12): MCP ``task_create`` — префикс из плана, занятый ID без SQL.

Тул больше не требует ``task_id``/``id_prefix``: префикс выводит сервис.
Занятый явный ``task_id`` уходит наружу структурной
``DuplicateTaskIdError`` с ``next_free_id``, а не сырым ``IntegrityError``.
Эталоны — литералы; ожидание вызовом ``id_prefix_for_plan``/``_next_task_id``
запрещено спекой.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.domain.entities import Plan, PlanSection, Priority, TaskType
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import TaskModel
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import task_service
from cod_doc.services.task_service import DuplicateTaskIdError
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_SCOPE = "adoption-2026-08"
_TASKS_AFTER_SEED = 1


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _seed(session: Session) -> int:
    """Проект, план ``adoption-2026-08`` с секцией A и задачей ADO-003."""
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="pr", title="pr", root_path="/tmp/pr", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    plan = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope=_SCOPE, principle="test-first")
    )
    plan.created = now
    plan.last_updated = now
    session.flush()
    section = PlanSectionRepository(session).add(
        PlanSection(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    )
    session.flush()
    task_service.create(
        session,
        project_id=proj.row_id,
        plan_id=plan.row_id,
        section_id=section.row_id,
        title="seeded task",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="test",
        task_id="ADO-003",
    )
    return proj.row_id


def _task_create(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> Any:
    from cod_doc.mcp.tools import task_tools

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        proj_id = _seed(s)
    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return mcp._tool_manager._tools["task_create"].fn


def _task_count(engine: Engine) -> int:
    with transactional(make_session_factory(engine)) as s:
        return s.execute(select(func.count()).select_from(TaskModel)).scalar_one()


def test_task_create_without_id_or_prefix(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    task_create = _task_create(monkeypatch, engine)

    result = task_create(
        project="pr",
        plan_scope=_SCOPE,
        section_letter="A",
        title="AFT-011 derived prefix",
        type="feature",
        priority="medium",
    )

    assert result["task_id"] == "ADO-004"


def test_task_create_taken_id_is_structured(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_create = _task_create(monkeypatch, engine)

    with pytest.raises(DuplicateTaskIdError) as info:
        task_create(
            project="pr",
            plan_scope=_SCOPE,
            section_letter="A",
            title="AFT-011 taken id",
            type="feature",
            priority="medium",
            task_id="ADO-003",
        )

    assert info.value.next_free_id == "ADO-004"
    text = str(info.value)
    for leaked in ("INSERT", "UNIQUE constraint", "sqlite3"):
        assert leaked not in text


def test_task_create_dry_run_auto_id_writes_nothing(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_create = _task_create(monkeypatch, engine)

    result = task_create(
        project="pr",
        plan_scope=_SCOPE,
        section_letter="A",
        title="AFT-011 dry run",
        type="feature",
        priority="medium",
        dry_run=True,
    )

    assert result["task_id"] == "ADO-004"
    assert result["dry_run"] is True
    assert _task_count(engine) == _TASKS_AFTER_SEED
