"""PCA-946: task_create_many — batch create in a single transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.mcp.tools import task_tools


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="bm", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="bm-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _stub(monkeypatch, factory) -> None:
    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)


def test_create_many_happy_path(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    create_many = _get_tool(mcp, "task_create_many")

    result = create_many(
        project="bm",
        plan_scope="bm-plan",
        section_letter="A",
        items=[
            {"title": "Task A", "type": "feature", "priority": "high", "id_prefix": "BMP"},
            {"title": "Task B", "type": "feature", "priority": "medium", "id_prefix": "BMP"},
            {"title": "Task C", "type": "test", "priority": "low", "id_prefix": "BMP"},
        ],
    )
    assert result["committed"] is True
    assert len(result["created"]) == 3
    assert result["errors"] == []

    with transactional(factory) as session:
        rows = (
            session.execute(
                select(TaskModel).where(TaskModel.title.in_(["Task A", "Task B", "Task C"]))
            )
            .scalars()
            .all()
        )
    assert len(rows) == 3


def test_create_many_rollback_on_error(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    create_many = _get_tool(mcp, "task_create_many")

    result = create_many(
        project="bm",
        plan_scope="bm-plan",
        section_letter="A",
        items=[
            {"title": "Good 1", "type": "feature", "priority": "high", "id_prefix": "BMR"},
            {
                "title": "",
                "type": "feature",
                "priority": "high",
                "id_prefix": "BMR",
            },  # missing title
            {"title": "Good 2", "type": "feature", "priority": "high", "id_prefix": "BMR"},
        ],
    )
    assert result["committed"] is False
    # Default continue_on_error=False → whole batch rolled back.
    with transactional(factory) as session:
        rows = (
            session.execute(select(TaskModel).where(TaskModel.title.in_(["Good 1", "Good 2"])))
            .scalars()
            .all()
        )
    assert rows == [], "batch should be rolled back on first error"


def test_create_many_continue_on_error_commits_good_ones(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    create_many = _get_tool(mcp, "task_create_many")

    result = create_many(
        project="bm",
        plan_scope="bm-plan",
        section_letter="A",
        items=[
            {"title": "Survives 1", "type": "feature", "priority": "high", "id_prefix": "BMC"},
            {"title": "", "type": "feature", "priority": "high", "id_prefix": "BMC"},  # bad
            {"title": "Survives 2", "type": "feature", "priority": "high", "id_prefix": "BMC"},
        ],
        continue_on_error=True,
    )
    assert result["committed"] is True
    assert len(result["created"]) == 2
    assert len(result["errors"]) == 1

    with transactional(factory) as session:
        rows = (
            session.execute(
                select(TaskModel).where(TaskModel.title.in_(["Survives 1", "Survives 2"]))
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
