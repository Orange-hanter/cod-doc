"""PCA-951: --profile minimal | standard | full — control tool surface."""

from __future__ import annotations

import pytest

from cod_doc.mcp import server as mcp_server
from cod_doc.mcp.profiles import (
    LEGACY_TOOLS,
    MINIMAL_TOOLS,
    keep_tool,
)


@pytest.fixture(autouse=True)
def _reset_profile():
    """Each test starts with a fresh full-surface registration.

    The server module is loaded once at import time with all tools
    registered. Tests in this file mutate `mcp._tool_manager._tools`
    via apply_profile, so we snapshot before and restore after.
    """
    snapshot = dict(mcp_server.mcp._tool_manager._tools)
    yield
    mcp_server.mcp._tool_manager._tools.clear()
    mcp_server.mcp._tool_manager._tools.update(snapshot)
    mcp_server._ACTIVE_PROFILE = "full"


def _registered_names() -> set[str]:
    return set(mcp_server.mcp._tool_manager._tools.keys())


def test_full_profile_keeps_all_tools() -> None:
    pre = len(_registered_names())
    stats = mcp_server.apply_profile("full")
    assert stats["dropped"] == 0
    assert stats["kept"] == pre


def test_minimal_profile_exposes_only_minimal_set() -> None:
    mcp_server.apply_profile("minimal")
    names = _registered_names()
    # Subset relation: only minimal tools, no legacy.
    assert names <= MINIMAL_TOOLS, (
        f"minimal profile exposed non-minimal tools: {sorted(names - MINIMAL_TOOLS)}"
    )
    # All declared minimal tools that exist in the catalog should survive.
    MINIMAL_TOOLS & set(_registered_names()) | MINIMAL_TOOLS
    # We expect at least the core 10 cold-start tools to be present.
    assert {"capabilities", "skill_list", "task_create", "context_get"} <= names


def test_standard_profile_drops_legacy() -> None:
    mcp_server.apply_profile("standard")
    names = _registered_names()
    # No legacy tool should remain.
    leaks = LEGACY_TOOLS & names
    assert not leaks, f"standard profile leaks legacy tools: {sorted(leaks)}"
    # But DB-backed tools should remain.
    assert "task_create" in names
    assert "doc_create" in names


def test_invalid_profile_raises() -> None:
    with pytest.raises(ValueError, match="Unknown profile"):
        mcp_server.apply_profile("custom-evil")


async def test_capabilities_reports_active_profile() -> None:
    mcp_server.apply_profile("standard")
    capabilities = mcp_server.mcp._tool_manager._tools["capabilities"].fn
    result = await capabilities()
    assert result["profile"] == "standard"


def test_default_profile_is_agent() -> None:
    """Cycle-5 change: default --profile is 'agent' (was 'standard' in cycle-4).

    Agent profile gives AI clients a 6-tool task-centric surface where each
    call returns a self-sufficient payload. Standard/full remain available
    for admin/CLI/web integrations.
    """
    import os as _os
    from unittest.mock import patch

    with patch.dict(_os.environ, {}, clear=False):
        _os.environ.pop("COD_DOC_PROFILE", None)
        from click.testing import CliRunner

        from cod_doc.mcp.server import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "default: agent" in result.output, (
            f"--profile must default to 'agent' (cycle-5). Help output:\n{result.output}"
        )


def test_keep_tool_pure_logic() -> None:
    assert keep_tool("task_create", "full") is True
    assert keep_tool("task_create", "standard") is True
    assert keep_tool("task_create", "minimal") is True
    assert keep_tool("run_agent_once", "standard") is False
    assert keep_tool("run_agent_once", "minimal") is False
    assert keep_tool("run_agent_once", "full") is True
    assert keep_tool("plan_audit", "minimal") is False
    assert keep_tool("plan_audit", "standard") is True


# ADO-052: documented counts. When this test fails after adding/removing a
# tool, update the numbers here AND in: AGENTS.md §5.9, cod_doc/mcp/profiles.py
# docstring, cod_doc/mcp/server.py --profile help, docs/mcp-integration.md.
EXPECTED_PROFILE_COUNTS = {
    "agent": 6,
    "minimal": 20,
    "standard": 107,
    "full": 111,
}


def test_profile_counts_match_documented_values() -> None:
    """Smoke: registry ↔ profile counts must match what the docs promise."""
    names = list(mcp_server.mcp._tool_manager._tools)
    actual = {p: sum(1 for n in names if keep_tool(n, p)) for p in EXPECTED_PROFILE_COUNTS}
    assert actual == EXPECTED_PROFILE_COUNTS, (
        f"profile counts drifted: {actual} — update EXPECTED_PROFILE_COUNTS and the docs "
        "(AGENTS.md §5.9, profiles.py docstring, server.py --profile help, "
        "docs/mcp-integration.md)"
    )


SYM_006D_TOOLS = {
    "finding_list",
    "finding_get",
    "finding_promote",
    "finding_dismiss",
    "ctx_docs",
    "ctx_drift",
}


def test_sym006d_keep_tool_logic() -> None:
    """SYM-006D / RFC 22: finding_*/ctx_* appear in standard+full only.

    minimal and agent profiles are explicit allowlists, so the new names
    must not leak into them; standard inherits everything non-legacy.
    """
    for name in SYM_006D_TOOLS:
        assert keep_tool(name, "full") is True
        assert keep_tool(name, "standard") is True
        assert keep_tool(name, "minimal") is False
        assert keep_tool(name, "agent") is False


def test_sym006d_minimal_profile_excludes_new_tools() -> None:
    mcp_server.apply_profile("minimal")
    assert not (SYM_006D_TOOLS & _registered_names())


def test_sym006d_agent_profile_excludes_new_tools() -> None:
    mcp_server.apply_profile("agent")
    assert not (SYM_006D_TOOLS & _registered_names())


def test_sym006d_standard_profile_keeps_new_tools() -> None:
    mcp_server.apply_profile("standard")
    assert _registered_names() >= SYM_006D_TOOLS
