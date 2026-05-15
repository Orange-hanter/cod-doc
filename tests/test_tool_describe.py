"""Cycle-4: tool_describe(name) — middle layer between capabilities() and tools/list."""

from __future__ import annotations

from typing import Any

from cod_doc.mcp.server import mcp as live_mcp


def _get_tool(name: str) -> Any:
    return live_mcp._tool_manager._tools[name].fn


def test_describe_known_tool_returns_full_contract() -> None:
    describe = _get_tool("tool_describe")
    result = describe(name="task_create")
    assert result["found"] is True
    assert result["name"] == "task_create"
    assert result["family"] == "task"
    assert "input_schema" in result
    assert "project" in result["required_params"]
    assert isinstance(result["optional_params"], list)
    # Some plan-aware fields are required for task_create:
    assert "plan_scope" in result["required_params"]
    assert "section_letter" in result["required_params"]
    assert result["deprecated"] is False


def test_describe_unknown_returns_hint() -> None:
    describe = _get_tool("tool_describe")
    result = describe(name="totally_made_up_tool")
    assert result["found"] is False
    assert "hint" in result
    assert "tool_search" in result["related_tools"]


def test_describe_includes_related_tools_from_same_family() -> None:
    describe = _get_tool("tool_describe")
    result = describe(name="task_create")
    related = result["related_tools"]
    assert any(r.startswith("task_") for r in related)
    # Must not include itself.
    assert "task_create" not in related


def test_describe_deprecated_legacy_tool_flagged() -> None:
    describe = _get_tool("tool_describe")
    result = describe(name="add_task")  # DEPRECATED legacy
    assert result["found"] is True
    assert result["deprecated"] is True
