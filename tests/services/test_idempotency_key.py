"""PCA-948: idempotency_key returns cached result on retry."""

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
from cod_doc.mcp.tools import _idempotency, task_tools


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="ip", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="ip-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def test_task_create_with_same_idempotency_key_returns_cached(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    _idempotency.clear()
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)

    mcp = FastMCP("test")
    task_tools.register(mcp)
    create = _get_tool(mcp, "task_create")

    args = dict(
        project="ip",
        plan_scope="ip-plan",
        section_letter="A",
        title="idem",
        type="feature",
        priority="medium",
        id_prefix="IDM",
        idempotency_key="run-42-op-1",
    )

    first = create(**args)
    second = create(**args)

    assert first["task_id"] == second["task_id"]
    assert second.get("idempotent_replay") is True
    assert "idempotent_replay" not in first

    # Only ONE task row exists in DB.
    with transactional(factory) as session:
        rows = session.execute(select(TaskModel).where(TaskModel.title == "idem")).scalars().all()
    assert len(rows) == 1, f"expected single row, got {len(rows)}"


def test_different_idempotency_keys_create_separate_tasks(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    _idempotency.clear()
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)

    mcp = FastMCP("test")
    task_tools.register(mcp)
    create = _get_tool(mcp, "task_create")

    args = dict(
        project="ip",
        plan_scope="ip-plan",
        section_letter="A",
        type="feature",
        priority="medium",
        id_prefix="IDK",
        allow_duplicate=True,
    )
    create(**args, title="A1", idempotency_key="k1")
    create(**args, title="A2", idempotency_key="k2")

    with transactional(factory) as session:
        rows = (
            session.execute(select(TaskModel).where(TaskModel.title.in_(["A1", "A2"])))
            .scalars()
            .all()
        )
    assert len(rows) == 2


def test_no_idempotency_key_does_not_cache(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """Без idempotency_key duplicate-guard от title по-прежнему работает."""
    _idempotency.clear()
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)

    mcp = FastMCP("test")
    task_tools.register(mcp)
    create = _get_tool(mcp, "task_create")

    args = dict(
        project="ip",
        plan_scope="ip-plan",
        section_letter="A",
        title="no-key",
        type="feature",
        priority="medium",
        id_prefix="NOK",
    )
    create(**args)  # no idempotency_key
    # Second call without key but same title hits duplicate-guard.
    import pytest

    with pytest.raises(ValueError, match="duplicate_of"):
        create(**args)
