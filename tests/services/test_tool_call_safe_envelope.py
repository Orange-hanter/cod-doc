"""PCA-943: tool_call_safe proxy returns unified envelope shape."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.mcp.server import mcp as live_mcp


def _get_tool(name: str) -> Any:
    return live_mcp._tool_manager._tools[name].fn


def _seed(session) -> None:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="sp", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="sp-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()


async def test_envelope_unknown_tool_name() -> None:
    safe = _get_tool("tool_call_safe")
    result = await safe(tool_name="totally_not_a_tool", args={})
    assert result["ok"] is False
    assert result["result"] is None
    assert result["error"]["code"] == "tool_not_found"
    assert "tool_search" in result["error"]["related_tools"]


async def test_envelope_success_path_for_capabilities() -> None:
    safe = _get_tool("tool_call_safe")
    result = await safe(tool_name="capabilities", args={})
    assert result["ok"] is True
    assert result["error"] is None
    assert isinstance(result["result"], dict)
    assert "cod_doc_version" in result["result"]


async def test_envelope_validation_error_for_unknown_task(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)

    safe = _get_tool("tool_call_safe")
    result = await safe(
        tool_name="task_update_status",
        args={"project": "sp", "task_id": "NOPE-999", "new_status": "done"},
    )
    assert result["ok"] is False
    # Unknown task → wrapped as not_found OR validation (ValueError path).
    assert result["error"]["code"] in {"not_found", "validation"}
    assert "NOPE-999" in result["error"]["message"]


async def test_duplicate_task_id_maps_to_conflict(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """AFT-011: занятый task_id → conflict с next_free_id, без SQL в конверте."""
    from cod_doc.domain.entities import Priority, TaskType
    from cod_doc.services import task_service

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
        task_service.create(
            session,
            project_id=1,
            plan_id=1,
            section_id=1,
            title="seeded",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="test",
            task_id="SP-001",
        )

    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)

    safe = _get_tool("tool_call_safe")
    result = await safe(
        tool_name="task_create",
        args={
            "project": "sp",
            "plan_scope": "sp-plan",
            "section_letter": "A",
            "title": "another",
            "type": "feature",
            "priority": "medium",
            "task_id": "SP-001",
        },
    )
    assert result["ok"] is False
    error = result["error"]
    assert error["code"] == "conflict"
    assert error["retry_safe"] is False
    assert "SP-002" in error["message"]
    assert "SP-002" in error["hint"]
    assert "INSERT" not in error["message"]


async def test_integrity_error_maps_to_conflict(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AFT-011: сырой IntegrityError → conflict, текст SQL-выражения не утекает."""
    import sqlite3

    from sqlalchemy.exc import IntegrityError

    statement = "INSERT INTO task (project_id, task_id) VALUES (?, ?)"

    def _raise(**_: Any) -> None:
        raise IntegrityError(
            statement,
            (1, "SP-001"),
            sqlite3.IntegrityError("UNIQUE constraint failed: task.project_id, task.task_id"),
        )

    monkeypatch.setattr(live_mcp._tool_manager._tools["task_summary"], "fn", _raise)

    safe = _get_tool("tool_call_safe")
    result = await safe(tool_name="task_summary", args={"project": "sp"})
    assert result["ok"] is False
    error = result["error"]
    assert error["code"] == "conflict"
    assert error["retry_safe"] is False
    assert statement not in error["message"]
    assert "INSERT" not in error["message"]
