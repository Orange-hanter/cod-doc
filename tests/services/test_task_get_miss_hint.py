"""PCA-938: ``task.get`` на miss возвращает structured hint, не bare null.

Раньше тул отдавал ``null`` для несуществующего ``task_id`` — агент не
знал, что делать дальше. Теперь возвращает ``{task_id: None, found: False,
hint: ..., related_tools: [...]}`` со ссылкой на task.list / task.find_duplicate.

Также проверяем, что на HIT shape не изменилась (есть task_id != None).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Priority, Task, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.mcp.tools import task_tools
from cod_doc.services import task_service


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="X", slug="A-X", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def test_task_get_miss_returns_structured_hint(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    # Stub session_factory + require_project_id so the MCP wrapper uses our DB.
    from cod_doc.mcp.tools import task_tools as tt
    monkeypatch.setattr(tt, "session_factory", lambda project: (factory, None))

    mcp = FastMCP("test")
    task_tools.register(mcp)
    task_get = _get_tool(mcp, "task_get")

    result = task_get(project="any", task_id="NOPE-999")

    assert isinstance(result, dict), f"expected dict, got {type(result)}"
    assert result["found"] is False
    assert result["task_id"] is None
    assert result["requested_task_id"] == "NOPE-999"
    assert "NOPE-999" in result["hint"]
    assert "any" in result["hint"]
    assert "task_list" in result["related_tools"]
    assert "task_find_duplicate" in result["related_tools"]


def test_task_get_hit_returns_task_dict_unchanged(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed(session)
        task_service.create(
            session,
            project_id=proj_id,
            plan_id=plan_id,
            section_id=sec_id,
            task_id="HIT-001",
            title="Existing",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human",
        )

    from cod_doc.mcp.tools import task_tools as tt
    monkeypatch.setattr(tt, "session_factory", lambda project: (factory, None))

    mcp = FastMCP("test")
    task_tools.register(mcp)
    task_get = _get_tool(mcp, "task_get")

    result = task_get(project="p", task_id="HIT-001")

    assert isinstance(result, dict)
    # Hit shape: task_id present (not None), no "found"/"hint"/"related_tools".
    assert result.get("task_id") == "HIT-001"
    assert "hint" not in result
    assert "related_tools" not in result
