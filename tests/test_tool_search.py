"""PCA-942: tool_search() — semantic-lite discovery over MCP catalog."""

from __future__ import annotations

from typing import Any

from cod_doc.mcp.server import mcp as live_mcp


def _get_tool(name: str) -> Any:
    return live_mcp._tool_manager._tools[name].fn


def test_create_task_query_ranks_task_create_first() -> None:
    search = _get_tool("tool_search")
    results = search(query="create task", limit=5)
    assert results, "expected at least one result for 'create task'"
    assert results[0]["name"] == "task_create", (
        f"task_create should rank first for 'create task'. Top: {results[0]}"
    )


def test_mark_done_query_finds_task_complete() -> None:
    search = _get_tool("tool_search")
    results = search(query="mark done", limit=5)
    names = [r["name"] for r in results]
    assert "task_complete" in names, (
        f"expected task_complete in top results for 'mark done', got {names}"
    )


def test_family_filter_restricts_results() -> None:
    search = _get_tool("tool_search")
    results = search(query="list", limit=10, family="plan")
    for r in results:
        assert r["family"] == "plan", f"family filter broken: got {r['family']} for {r['name']}"


def test_results_carry_required_params_and_flags() -> None:
    search = _get_tool("tool_search")
    results = search(query="create task", limit=1)
    r = results[0]
    assert isinstance(r["required_params"], list)
    assert isinstance(r["deprecated"], bool)
    assert "score" in r
    assert "description" in r


def test_empty_query_returns_empty_list() -> None:
    search = _get_tool("tool_search")
    results = search(query="", limit=5)
    assert results == []


def test_deprecated_legacy_tool_flagged() -> None:
    """add_task is legacy/deprecated — must surface that flag."""
    search = _get_tool("tool_search")
    results = search(query="DEPRECATED legacy", limit=10)
    assert any(r["name"] == "add_task" and r["deprecated"] for r in results), (
        f"add_task should appear with deprecated=True. Got: {results}"
    )
