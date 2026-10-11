"""STO-023 на MCP-поверхности: ``task_create`` и ``story_get`` скоупятся проектом.

Сервисная сторона тех же поисков — ``tests/services/test_project_scoped_lookups.py``.
Тулы берутся из ``mcp._tool_manager._tools`` — так их зовут все MCP-тесты
репозитория (``tests/test_*_mcp.py``); ``call_tool`` сериализует ответ в
контент-блоки, а здесь проверяется сам словарь тула.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.infra.db import make_engine, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, TaskModel
from tests._alembic import run_alembic
from tests._project_scoped_seed import SCOPE, SLUGS, STORY, seed_two_projects

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _mcp_tools(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> dict[str, Callable[..., Any]]:
    """task_create / story_get на свежем FastMCP; слаг резолвится по БД, как в бою."""
    from cod_doc.mcp.tools import story_tools, task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(story_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    task_tools.register(mcp)
    story_tools.register(mcp)
    return {name: mcp._tool_manager._tools[name].fn for name in ("task_create", "story_get")}


@pytest.mark.parametrize("slug", SLUGS)
def test_mcp_task_create_lands_in_own_project_plan(
    monkeypatch: pytest.MonkeyPatch, engine: Engine, slug: str
) -> None:
    factory, pids = seed_two_projects(engine)
    tools = _mcp_tools(monkeypatch, factory)

    out = tools["task_create"](
        project=slug,
        plan_scope=SCOPE,
        section_letter="A",
        title=f"Task for {slug}",
        type="feature",
        priority="medium",
        task_id="BOOT-001",
    )
    assert out["plan_scope"] == SCOPE

    with transactional(factory) as session:
        row = session.execute(
            select(TaskModel.project_id, PlanModel.project_id, PlanSectionModel.title)
            .join(PlanModel, PlanModel.row_id == TaskModel.plan_id)
            .join(PlanSectionModel, PlanSectionModel.row_id == TaskModel.section_id)
            .where(TaskModel.title == f"Task for {slug}")
        ).one()
    assert tuple(row) == (pids[slug], pids[slug], f"Core {slug}")


@pytest.mark.parametrize("slug", SLUGS)
def test_mcp_story_get_returns_own_project_story(
    monkeypatch: pytest.MonkeyPatch, engine: Engine, slug: str
) -> None:
    factory, _ = seed_two_projects(engine)
    tools = _mcp_tools(monkeypatch, factory)

    out = tools["story_get"](project=slug, story_id=STORY)
    assert out is not None
    assert out["persona"] == f"persona-{slug}"
