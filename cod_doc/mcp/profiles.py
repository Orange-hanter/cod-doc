"""PCA-951 / AGT-001: MCP server profiles — control which tools are exposed.

Four profiles (counts validated by tests/test_server_profiles.py::
test_profile_counts_match_documented_values — keep them in sync with
AGENTS.md §5.9, server.py --profile help, docs/mcp-integration.md):

- ``agent`` (cycle-5, **default**) — 6-tool task-centric surface for AI
  agents. Each call returns a self-sufficient payload (task card with
  inlined skills, related docs, navigation) so the agent doesn't need
  5-10 round-trips to collect context. The recommended profile for
  AI-driven workflows.
- ``minimal`` — 21-tool cold-start surface for non-agent integrations
  that still want a curated subset of CRUD tools.
- ``standard`` — 112-tool DB-backed surface; drops only the remaining
  legacy YAML-backed agent tools (run_agent_once, get_agent_context, …).
  The legacy YAML CRUD tools were removed in STB-002 (2026-06-08) once
  the DB became the source of truth.
- ``full`` — all 116 tools the server registers, including the remaining
  legacy agent tools. For admin / migration / debugging sessions.

Active profile is chosen at server start via CLI ``--profile`` or env
``COD_DOC_PROFILE`` (default: ``agent``).
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Agent — 6 task-centric tools for AI workflows (AGT-001).                    #
# Each tool returns a self-sufficient payload so the agent doesn't need to    #
# chain calls for context. Internal CRUD lives under standard/full.           #
# --------------------------------------------------------------------------- #

AGENT_TOOLS: frozenset[str] = frozenset(
    {
        # L0 entry-point: what server / skills / enums / session state.
        "agent_capabilities",
        # Atomic: ready-set → checkout → assemble task card.
        "agent_pick",
        # Opt-in deep fetch when card didn't include something.
        "agent_get",
        # Unified dispatcher: progress | blocker | approval_request.
        "agent_report",
        # Guarded done: validates blockers, releases lock.
        "agent_complete",
        # Give up without done; releases lock, status → todo.
        "agent_release",
    }
)


# --------------------------------------------------------------------------- #
# Minimal — small enough to fit in an agent's "what tools do you have" prompt. #
# --------------------------------------------------------------------------- #

MINIMAL_TOOLS: frozenset[str] = frozenset(
    {
        # Discovery / bootstrap.
        "capabilities",
        "skill_list",
        "skill_get",
        "tool_search",
        "context_get",
        # Project state.
        "task_list",
        "task_get",
        "task_create",
        "task_update_status",
        "task_checkout",
        "task_release",
        "task_complete",
        "task_next_ready",
        "plan_ready",
        "plan_sections_list",
        # Cycle-4 discovery middle layer.
        "tool_describe",
        # Cycle-4 session defaults.
        "set_default_project",
        "get_default_project",
        # Documents.
        "doc_list",
        "doc_body",
        "structure_context",
    }
)


# --------------------------------------------------------------------------- #
# Legacy YAML-backed tools — hidden under "standard" profile.                 #
# --------------------------------------------------------------------------- #

LEGACY_TOOLS: frozenset[str] = frozenset(
    {
        # legacy_agent_tools — kept after STB-002 (2026-06-08). The YAML CRUD
        # surfaces (legacy_project/master/search/resources) were removed once
        # the DB became the source of truth; run_agent_once remains the
        # approval/resume entry point.
        "run_agent_once",
        "get_agent_context",
        "clear_agent_context",
        "check_config",
    }
)


# --------------------------------------------------------------------------- #
# Profile resolution                                                            #
# --------------------------------------------------------------------------- #

VALID_PROFILES: frozenset[str] = frozenset({"agent", "minimal", "standard", "full"})


def keep_tool(name: str, profile: str) -> bool:
    """Return True iff ``name`` should be exposed under ``profile``."""
    if profile == "full":
        return True
    if profile == "agent":
        return name in AGENT_TOOLS
    if profile == "minimal":
        return name in MINIMAL_TOOLS
    if profile == "standard":
        return name not in LEGACY_TOOLS
    raise ValueError(f"Unknown profile: {profile!r}; expected one of {sorted(VALID_PROFILES)}")
