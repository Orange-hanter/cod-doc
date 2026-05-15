"""PCA-951: MCP server profiles — control which tools are exposed.

Three profiles:

- ``minimal`` — 13-tool cold-start surface: agent has just what's needed
  to onboard, discover, pick a task, and execute it. Aimed at fresh
  integrations / lightweight orchestrators.
- ``standard`` — ``minimal`` + the full DB-backed surface; drops the
  legacy YAML-backed tools (add_task, list_tasks, get_master, …) that
  are deprecated. Recommended default for new integrations.
- ``full`` — every tool the server registers, including legacy.
  For admin / migration / debugging sessions.

Active profile is chosen at server start via CLI ``--profile`` or env
``COD_DOC_PROFILE``. Defaults to ``full`` for backward compatibility
with existing integrations.
"""

from __future__ import annotations

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

VALID_PROFILES: frozenset[str] = frozenset({"minimal", "standard", "full"})


def keep_tool(name: str, profile: str) -> bool:
    """Return True iff ``name`` should be exposed under ``profile``."""
    if profile == "full":
        return True
    if profile == "minimal":
        return name in MINIMAL_TOOLS
    if profile == "standard":
        return name not in LEGACY_TOOLS
    raise ValueError(f"Unknown profile: {profile!r}; expected one of {sorted(VALID_PROFILES)}")
