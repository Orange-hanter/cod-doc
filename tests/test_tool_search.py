"""PCA-942: tool_search() — semantic-lite discovery over MCP catalog."""

from __future__ import annotations

from typing import Any

from cod_doc.mcp.server import mcp as live_mcp


def _get_tool(name: str) -> Any:
    return live_mcp._tool_manager._tools[name].fn


async def test_create_task_query_ranks_task_create_first() -> None:
    search = _get_tool("tool_search")
    results = await search(query="create task", limit=5)
    assert results, "expected at least one result for 'create task'"
    assert results[0]["name"] == "task_create", (
        f"task_create should rank first for 'create task'. Top: {results[0]}"
    )


async def test_mark_done_query_finds_task_complete() -> None:
    search = _get_tool("tool_search")
    results = await search(query="mark done", limit=5)
    names = [r["name"] for r in results]
    assert "task_complete" in names, (
        f"expected task_complete in top results for 'mark done', got {names}"
    )


async def test_family_filter_restricts_results() -> None:
    search = _get_tool("tool_search")
    results = await search(query="list", limit=10, family="plan")
    for r in results:
        assert r["family"] == "plan", f"family filter broken: got {r['family']} for {r['name']}"


async def test_results_carry_required_params_and_flags() -> None:
    search = _get_tool("tool_search")
    results = await search(query="create task", limit=1)
    r = results[0]
    assert isinstance(r["required_params"], list)
    assert isinstance(r["deprecated"], bool)
    assert "score" in r
    assert "description" in r


async def test_empty_query_returns_empty_list() -> None:
    search = _get_tool("tool_search")
    results = await search(query="", limit=5)
    assert results == []


async def test_deprecated_tool_flagged() -> None:
    """A tool whose description marks DEPRECATED must surface that flag.

    STB-002 removed the legacy YAML CRUD tools; adr_deprecate now stands in as
    a stable tool whose description trips the ``DEPRECATED`` heuristic.
    """
    search = _get_tool("tool_search")
    results = await search(query="DEPRECATED", limit=10)
    assert any(r["name"] == "adr_deprecate" and r["deprecated"] for r in results), (
        f"adr_deprecate should appear with deprecated=True. Got: {results}"
    )
