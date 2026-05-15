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
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="A", slug="A", position=0
    )
    session.add(sec)
    session.flush()


def test_envelope_unknown_tool_name() -> None:
    safe = _get_tool("tool_call_safe")
    result = safe(tool_name="totally_not_a_tool", args={})
    assert result["ok"] is False
    assert result["result"] is None
    assert result["error"]["code"] == "tool_not_found"
    assert "tool_search" in result["error"]["related_tools"]


def test_envelope_success_path_for_capabilities() -> None:
    safe = _get_tool("tool_call_safe")
    result = safe(tool_name="capabilities", args={})
    assert result["ok"] is True
    assert result["error"] is None
    assert isinstance(result["result"], dict)
    assert "cod_doc_version" in result["result"]


def test_envelope_validation_error_for_unknown_task(
    engine_with_schema, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    from cod_doc.mcp.tools import task_tools
    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)

    safe = _get_tool("tool_call_safe")
    result = safe(
        tool_name="task_update_status",
        args={"project": "sp", "task_id": "NOPE-999", "new_status": "done"},
    )
    assert result["ok"] is False
    # Unknown task → wrapped as not_found OR validation (ValueError path).
    assert result["error"]["code"] in {"not_found", "validation"}
    assert "NOPE-999" in result["error"]["message"]
