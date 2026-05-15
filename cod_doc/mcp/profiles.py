"""PCA-951 / AGT-001: MCP server profiles — control which tools are exposed.

Four profiles:

- ``agent`` (cycle-5) — 6-tool task-centric surface for AI agents. Each
  call returns a self-sufficient payload (task card with inlined skills,
  related docs, navigation) so the agent doesn't need 5-10 round-trips
  to collect context. The recommended profile for AI-driven workflows.
- ``minimal`` — ~18-tool cold-start surface for non-agent integrations
  that still want a curated subset of CRUD tools.
- ``standard`` (current default) — full DB-backed surface; drops only
  the legacy YAML-backed tools (add_task, list_tasks, get_master, …).
- ``full`` — every tool the server registers, including legacy.
  For admin / migration / debugging sessions.

Active profile is chosen at server start via CLI ``--profile`` or env
``COD_DOC_PROFILE``.
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
    }
)


# --------------------------------------------------------------------------- #
# Legacy YAML-backed tools — hidden under "standard" profile.                 #
# --------------------------------------------------------------------------- #

LEGACY_TOOLS: frozenset[str] = frozenset(
    {
        # legacy_project_tools
        "list_projects",
        "get_project_status",
        "add_project",
        "remove_project",
        "list_tasks",
        "add_task",
        "update_task",
        "next_pending_task",
        # legacy_master_tools
        "get_master",
        "update_master_hashes",
        "check_stale_refs",
        "generate_ref",
        "read_context",
        "read_file",
        "list_files",
        "hash_file",
        "verify_hash",
        # legacy_agent_tools
        "run_agent_once",
        "get_agent_context",
        "clear_agent_context",
        "check_config",
        # legacy_search_tools
        "search_docs",
        "reindex",
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
