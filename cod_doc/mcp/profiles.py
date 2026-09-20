"""PCA-951 / AGT-001 / RFC 25: MCP server profiles — control which tools are exposed.

Four profiles (counts validated by tests/test_server_profiles.py::
test_profile_counts_match_documented_values — keep them in sync with
AGENTS.md §5.9, server.py --profile help, docs/mcp-integration.md):

- ``agent`` (RFC 25 §3.2/§3.5, **default**) — 6 curator tools for the
  default AI agent, whose role is documentation availability and search,
  not task execution: ``agent_capabilities`` announces the role,
  ``curator_next`` hands over the doc card (what is broken and what to do
  first), ``ctx_search`` / ``ctx_drift`` cover corpus search and drift,
  ``context_get`` assembles Snowball packages, ``agent_report`` escalates
  to a human. CUR-016 swapped ``ctx_docs`` out for ``curator_next``: a raw
  document listing is a strictly weaker answer to "what do I do now", and
  the card carries the same corpus slice inside its drift half. The
  cycle-5 task tools (``agent_pick``, ``agent_get``, ``agent_complete``,
  ``agent_release``) stay registered but are visible only under
  standard/full — implementation work belongs to a human or a coding
  agent on those profiles.
- ``minimal`` — 21-tool cold-start surface for non-agent integrations
  that still want a curated subset of CRUD tools.
- ``standard`` — 141-tool DB-backed surface; drops only the remaining
  legacy YAML-backed agent tools (run_agent_once, get_agent_context, …).
  The legacy YAML CRUD tools were removed in STB-002 (2026-06-08) once
  the DB became the source of truth.
- ``full`` — all 145 tools the server registers, including the remaining
  legacy agent tools. For admin / migration / debugging sessions.

Active profile is chosen at server start via CLI ``--profile`` or env
``COD_DOC_PROFILE`` (default: ``agent``).
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Agent — 6 curator tools for the default AI agent (RFC 25 §3.2/§3.5,        #
# CUR-008 + CUR-016).                                                        #
# The role is documentation availability and search, not task execution:     #
# read drift, search the corpus, assemble Snowball context, escalate.        #
# agent_pick / agent_get / agent_complete / agent_release stay registered    #
# but are visible only under standard/full, where a human or a coding agent  #
# does implementation work. Internal CRUD lives under standard/full too.     #
# --------------------------------------------------------------------------- #

AGENT_TOOLS: frozenset[str] = frozenset(
    {
        # L0 entry-point: role, forbidden calls, skills, enums, session state.
        "agent_capabilities",
        # FTS search over docs/ADR/stories/tasks; lazy reindex of empty index.
        "ctx_search",
        # Doc card (CUR-016): drift + broken links + stale MASTER hashes +
        # open findings, already ordered into a work queue. Replaced the raw
        # ctx_docs listing — same corpus slice, plus what to do with it.
        "curator_next",
        # Drift: markdown ↔ DB divergence the curator has to close.
        "ctx_drift",
        # Snowball context (L0/L1/L2) under a token budget.
        "context_get",
        # Unified dispatcher: progress | blocker | approval_request.
        "agent_report",
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
