"""AFT-012 (RFC 27 F13): task_create предупреждает об affects_files вне root_path.

MCP-поверхность ``task_create`` (сигнатура и тип возврата
``task_service.create`` не меняются) добавляет в ответ ключ ``warnings``,
когда часть затронутых файлов — абсолютные пути вне корня проекта. Создание
не отклоняется. Эталоны путей — литералы; ожидание вызовом
``foreign_paths``/``is_foreign`` запрещено спекой.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.domain.entities import Plan, PlanSection
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import TaskModel
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _seed(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="pr", title="pr", root_path="/tmp/pr", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    plan = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope="pr-plan", principle="test-first")
    )
    plan.created = now
    plan.last_updated = now
    session.flush()
    PlanSectionRepository(session).add(
        PlanSection(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
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


def test_task_create_warns_on_foreign_paths(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_create = _task_create(monkeypatch, engine)

    result = task_create(
        project="pr",
        plan_scope="pr-plan",
        section_letter="A",
        title="AFT-012 foreign paths",
        type="feature",
        priority="high",
        task_id="AFT-001",
        affects_files=["/elsewhere/a.py", "cod_doc/x.py"],
    )

    assert result["task_id"] == "AFT-001"
    warnings = result["warnings"]
    assert len(warnings) == 1
    assert "/elsewhere/a.py" in warnings[0]
    assert "cod_doc/x.py" not in warnings[0]


def test_task_create_no_warning_for_local_paths(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_create = _task_create(monkeypatch, engine)

    result = task_create(
        project="pr",
        plan_scope="pr-plan",
        section_letter="A",
        title="AFT-012 local paths",
        type="feature",
        priority="high",
        task_id="AFT-002",
        affects_files=["cod_doc/x.py", "/tmp/pr/y.py"],
    )

    assert result["task_id"] == "AFT-002"
    assert "warnings" not in result


def test_task_create_no_warning_without_files(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_create = _task_create(monkeypatch, engine)

    result = task_create(
        project="pr",
        plan_scope="pr-plan",
        section_letter="A",
        title="AFT-012 no files",
        type="feature",
        priority="high",
        task_id="AFT-003",
    )

    assert result["task_id"] == "AFT-003"
    assert "warnings" not in result


def test_task_create_dry_run_warns(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    task_create = _task_create(monkeypatch, engine)

    result = task_create(
        project="pr",
        plan_scope="pr-plan",
        section_letter="A",
        title="AFT-012 dry run",
        type="feature",
        priority="high",
        task_id="AFT-004",
        affects_files=["/elsewhere/a.py"],
        dry_run=True,
    )

    assert result["dry_run"] is True
    warnings = result["warnings"]
    assert len(warnings) == 1
    assert "/elsewhere/a.py" in warnings[0]

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        count = s.scalar(select(func.count()).select_from(TaskModel))
    assert count == 0
