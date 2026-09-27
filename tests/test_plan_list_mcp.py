"""AFT-007 (RFC 27 F8): MCP-тул plan_list — какие планы есть, без угадывания scope.

Тул зовётся на свежем FastMCP с подменённым session_factory; в одной БД два
проекта, ``alpha`` и ``beta`` — изоляция hub-режима. Эталоны — литералы:
построить ожидание вызовом сервиса значило бы сравнить функцию саму с собой.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

#: проект → план → статусы задач (единственная секция «A»).
_SEED: dict[str, dict[str, list[str]]] = {
    "alpha": {"plan-x": ["done", "todo"], "plan-y": []},
    "beta": {"plan-z": ["todo"]},
}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=url)
    eng = make_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def plan_list(monkeypatch: pytest.MonkeyPatch, engine: Engine) -> Callable[..., Any]:
    from cod_doc.mcp.tools import plan_tools

    factory = make_session_factory(engine)
    ids: dict[str, int] = {}
    counter = 0
    with transactional(factory) as s:
        for slug, plan_map in _SEED.items():
            project = ProjectModel(
                slug=slug, title=slug, root_path=f"/tmp/{slug}", created=_T0, updated=_T0
            )
            s.add(project)
            s.flush()
            ids[slug] = project.row_id
            for i, (scope, statuses) in enumerate(plan_map.items()):
                created = _T0 + timedelta(minutes=i)
                plan = PlanModel(
                    project_id=project.row_id,
                    scope=scope,
                    principle=f"principle of {scope}",
                    created=created,
                    last_updated=created,
                )
                s.add(plan)
                s.flush()
                section = PlanSectionModel(
                    plan_id=plan.row_id, letter="A", title="Section A", slug="A-Section", position=0
                )
                s.add(section)
                s.flush()
                for status in statuses:
                    counter += 1
                    s.add(
                        TaskModel(
                            project_id=project.row_id,
                            plan_id=plan.row_id,
                            section_id=section.row_id,
                            task_id=f"T-{counter:03d}",
                            title=f"task {counter}",
                            status=status,
                            type="feature",
                            priority="medium",
                            created=_T0,
                            last_updated=_T0,
                        )
                    )

    monkeypatch.setattr(plan_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(plan_tools, "require_project_id", lambda session, project: ids[project])
    mcp = FastMCP("test")
    plan_tools.register(mcp)
    return mcp._tool_manager._tools["plan_list"].fn  # type: ignore[no-any-return]


def test_plan_list_returns_project_plans(plan_list: Callable[..., Any]) -> None:
    result = plan_list(project="alpha")
    assert [r["scope"] for r in result] == ["plan-x", "plan-y"]
    for row in result:
        assert set(row) == {
            "scope",
            "principle",
            "status",
            "total",
            "done",
            "in_progress",
            "cancelled",
            "remaining",
        }
    plan_x = result[0]
    assert plan_x["total"] == 2
    assert plan_x["done"] == 1
    assert plan_x["principle"] == "principle of plan-x"


def test_plan_list_hub_isolation(plan_list: Callable[..., Any]) -> None:
    result = plan_list(project="beta")
    assert [r["scope"] for r in result] == ["plan-z"]
    scopes = {r["scope"] for r in result}
    assert "plan-x" not in scopes
    assert "plan-y" not in scopes


def test_plan_list_status_filter(plan_list: Callable[..., Any]) -> None:
    assert [r["scope"] for r in plan_list(project="alpha", status="empty")] == ["plan-y"]
    with pytest.raises(ValueError, match="bogus"):
        plan_list(project="alpha", status="bogus")
