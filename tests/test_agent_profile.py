"""Cycle-5 AGT-001 + AGT-002: agent profile + agent_capabilities."""

from __future__ import annotations

import pytest

from cod_doc.mcp.profiles import AGENT_TOOLS, VALID_PROFILES, keep_tool
from cod_doc.mcp.server import (
    apply_profile,
)
from cod_doc.mcp.server import (
    mcp as live_mcp,
)


@pytest.fixture(autouse=True)
def _reset_profile():
    """Each test starts with a fresh full-surface registration."""
    snapshot = dict(live_mcp._tool_manager._tools)
    yield
    live_mcp._tool_manager._tools.clear()
    live_mcp._tool_manager._tools.update(snapshot)
    import cod_doc.mcp.server as srv

    srv._ACTIVE_PROFILE = "full"


def _names() -> set[str]:
    return set(live_mcp._tool_manager._tools.keys())


# ----------------------------------------------------------------- #
# AGT-001                                                            #
# ----------------------------------------------------------------- #


def test_agent_profile_in_valid_set() -> None:
    assert "agent" in VALID_PROFILES


def test_agent_tools_frozenset_has_six_names() -> None:
    """RFC 25 §3.2/§3.5 (CUR-008 + CUR-016): still 6 names, curator ones.

    CUR-016 swapped ``ctx_docs`` for ``curator_next``: the doc card carries
    the same corpus slice in its drift half and adds the work queue, so the
    raw listing no longer earns one of the six slots.
    """
    assert len(AGENT_TOOLS) == 6
    expected = {
        "agent_capabilities",
        "curator_next",
        "ctx_search",
        "ctx_drift",
        "context_get",
        "agent_report",
    }
    assert expected == AGENT_TOOLS


def test_keep_tool_agent_profile() -> None:
    for name in AGENT_TOOLS:
        assert keep_tool(name, "agent") is True
    # Non-agent tools must be filtered out.
    assert keep_tool("task_create", "agent") is False
    assert keep_tool("doc_create", "agent") is False
    assert keep_tool("capabilities", "agent") is False  # admin-surface
    # RFC 25 §3.2: the cycle-5 task tools stay registered but leave the
    # default surface — the curator does not pick implementation tasks.
    assert keep_tool("agent_pick", "agent") is False
    assert keep_tool("ctx_search", "agent") is True
    assert keep_tool("curator_next", "agent") is True
    # CUR-016: registered, but no longer part of the default six.
    assert keep_tool("ctx_docs", "agent") is False
    assert keep_tool("ctx_docs", "standard") is True


def test_agent_profile_exposes_exactly_six_tools() -> None:
    apply_profile("agent")
    assert _names() == AGENT_TOOLS


def test_agent_profile_tools_all_registered_at_startup() -> None:
    """Before any apply_profile() filtering, all 6 must be present
    so the filter has something to keep."""
    pre = _names()
    for name in AGENT_TOOLS:
        assert name in pre, (
            f"agent tool {name!r} must be registered at server startup. "
            f"Since RFC 25 §3.2/§3.5 the curator surface spans four modules: "
            f"agent_capabilities/agent_report live in "
            f"cod_doc/mcp/tools/agent_tools.py, ctx_search/ctx_drift "
            f"in cod_doc/mcp/tools/doc_tools.py, context_get in "
            f"cod_doc/mcp/tools/context_tools.py, curator_next in "
            f"cod_doc/mcp/tools/curator_tools.py — check the matching "
            f"register()."
        )


# ----------------------------------------------------------------- #
# AGT-002                                                            #
# ----------------------------------------------------------------- #


def test_agent_capabilities_returns_slim_l0_payload() -> None:
    capabilities = live_mcp._tool_manager._tools["agent_capabilities"].fn
    result = capabilities()

    # Required keys for L0 bootstrap (role/forbidden added by RFC 25 §3.2).
    for key in (
        "server_version",
        "profile",
        "role",
        "forbidden",
        "skills",
        "task_status_canonical",
        "task_status_legacy_aliases",
        "default_project",
        "orchestrator_skill",
        "next_action_hint",
    ):
        assert key in result, f"agent_capabilities missing key: {key}"

    # RFC 25 §3.2: the payload itself declares the curator role, so a
    # client that never reads the orchestrator skill still knows the rules.
    assert result["role"] == "doc-curator"
    assert set(result["forbidden"]) == {"agent_pick", "task_checkout", "task_complete"}

    # Skills carry name + description only (no body — keep payload small).
    assert isinstance(result["skills"], list)
    assert result["skills"], "expected at least one skill"
    for skill in result["skills"]:
        assert set(skill.keys()) == {"name", "description"}, (
            f"skill entry must be name+description only, got: {sorted(skill.keys())}"
        )

    # 7-state taxonomy present.
    assert len(result["task_status_canonical"]) == 7
    assert "todo" in result["task_status_canonical"]
    assert "in_progress" in result["task_status_canonical"]

    # Legacy aliases present.
    assert result["task_status_legacy_aliases"]["pending"] == "todo"

    # Orchestrator ref + next action hint guide the agent forward — towards
    # drift and search, away from picking implementation tasks (RFC 25 §3.2).
    assert "SKILL.md" in result["orchestrator_skill"]
    hint = result["next_action_hint"]
    assert "ctx_search" in hint and "agent_pick" not in hint, (
        f"next_action_hint must route the curator to ctx_* tools, got: {hint!r}"
    )


def test_agent_capabilities_payload_under_4kb() -> None:
    """L0 must stay small — tight context budgets are the whole point."""
    import json

    capabilities = live_mcp._tool_manager._tools["agent_capabilities"].fn
    size = len(json.dumps(capabilities()).encode("utf-8"))
    assert size < 4096, (
        f"agent_capabilities payload is {size} bytes (>4096). "
        f"Trim something or move to agent_get(what=...)."
    )


async def test_agent_capabilities_strict_subset_vs_admin_capabilities() -> None:
    """agent_capabilities omits the admin-surface noise: total tool count,
    family breakdown, references mapping, session_meta etc."""
    agent_caps = live_mcp._tool_manager._tools["agent_capabilities"].fn()
    admin_caps = await live_mcp._tool_manager._tools["capabilities"].fn()

    # Keys NOT in agent_capabilities (kept admin-only).
    for admin_only in ("tools", "references", "server_name", "session"):
        assert admin_only not in agent_caps, (
            f"agent_capabilities leaks admin key {admin_only!r}; move to agent_get."
        )

    # Both surfaces agree on the 7-state taxonomy.
    admin_canonical = admin_caps["enums"]["task_status_canonical"]
    assert set(agent_caps["task_status_canonical"]) == set(admin_canonical)


# ----------------------------------------------------------------- #
# AGT-003..007 are implemented in agent_service.py; behavioural       #
# tests live in tests/services/test_agent_pick.py and                #
# tests/services/test_agent_workflow.py. Here we only assert that    #
# the MCP wrappers exist (registration contract).                    #
# ----------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tool_name",
    ["agent_pick", "agent_get", "agent_report", "agent_complete", "agent_release"],
)
def test_agent_tool_is_registered_callable(tool_name: str) -> None:
    tool = live_mcp._tool_manager._tools[tool_name]
    assert callable(tool.fn)
    # No NotImplementedError in docstring — implementation is live.
    assert "Pending implementation" not in (tool.description or "")
