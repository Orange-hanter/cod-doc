"""Native MCP server exposing COD-DOC project operations.

Provides a complete LLM interface to COD-DOC. Tool/resource/prompt
implementations live in `cod_doc.mcp.tools.*` modules; this file just
constructs the FastMCP instance, asks each module to register its
surface, and exposes a click-based entry point.

PCA-951: ``--profile minimal|standard|full`` (env ``COD_DOC_PROFILE``)
controls which tools are exposed after registration. See
``cod_doc/mcp/profiles.py``.
"""

from __future__ import annotations

import os

import click
from mcp.server.fastmcp import FastMCP

from cod_doc.logging_config import get_logger, setup_logging
from cod_doc.mcp.profiles import VALID_PROFILES, keep_tool
from cod_doc.mcp.tools import (
    activity_tools,
    adr_tools,
    agent_tools,
    approval_tools,
    checkout_tools,
    context_tools,
    doc_tools,
    finding_tools,
    legacy_agent_tools,
    link_tools,
    plan_tools,
    revision_tools,
    routine_tools,
    run_tools,
    skill_tools,
    story_tools,
    task_doc_tools,
    task_tools,
)

mcp = FastMCP("COD-DOC", json_response=True)

# Order doesn't matter for FastMCP — tool/resource/prompt names live in a
# flat namespace. Group registrations by surface for grep-ability.
for _module in (
    # Legacy YAML-backed agent surface (run_agent_once + context helpers).
    # YAML CRUD tools (project/master/search/resources) removed 2026-06-08
    # under STB-002 now that the DB is the source of truth; run_agent_once
    # is kept as the approval/resume entry point.
    legacy_agent_tools,
    # COD-032 DB-backed tools (doc.*, task.*, plan.*, story.*, link.*, revision.*).
    doc_tools,
    task_tools,
    plan_tools,
    story_tools,
    link_tools,
    revision_tools,
    # COD-033 context.get
    context_tools,
    # PCA-032 run.list / run.get
    run_tools,
    # PCA-003 skill.list / skill.get
    skill_tools,
    # PCA-101 task_doc.* (task-bound docs)
    task_doc_tools,
    # PCA-111 activity.* (unified audit timeline)
    activity_tools,
    # PCA-121 approval.* (first-class approvals)
    approval_tools,
    # PCA-200 task_checkout / task_release
    checkout_tools,
    # PCA-211 routine.* (cron-style health checks)
    routine_tools,
    # AGT-001..AGT-007 agent.* (cycle-5 task-centric surface)
    agent_tools,
    # ADR-002 adr.* (Architecture Decision Records)
    adr_tools,
    # RFC 22 / SYM-006D finding.* (external findings; ctx_* aliases live in doc_tools)
    finding_tools,
):
    _module.register(mcp)


_log = get_logger("mcp.profile")
_ACTIVE_PROFILE: str = "full"


def apply_profile(profile: str) -> dict[str, str | int]:
    """Filter the registered tool catalog to match ``profile``.

    Returns ``{"profile": ..., "kept": N, "dropped": N}`` for logging.
    Mutates ``mcp._tool_manager._tools`` in place; called once on server
    start from ``main()``.
    """
    if profile not in VALID_PROFILES:
        raise ValueError(f"Unknown profile: {profile!r}; expected one of {sorted(VALID_PROFILES)}")
    global _ACTIVE_PROFILE
    _ACTIVE_PROFILE = profile
    os.environ["COD_DOC_ACTIVE_PROFILE"] = profile

    tools = mcp._tool_manager._tools
    to_drop = [name for name in tools if not keep_tool(name, profile)]
    for name in to_drop:
        del tools[name]
    return {"profile": profile, "kept": len(tools), "dropped": len(to_drop)}


def get_active_profile() -> str:
    """Return the profile applied at server startup (default 'full')."""
    return _ACTIVE_PROFILE


@click.command()
@click.option("--transport", type=click.Choice(["stdio", "streamable-http"]), default="stdio")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, type=int, show_default=True)
@click.option(
    "--profile",
    type=click.Choice(sorted(VALID_PROFILES)),
    default=os.environ.get("COD_DOC_PROFILE", "agent"),
    show_default=True,
    help="Tool-surface profile (default: agent — cycle-5). agent=6 task-centric "
    "tools for AI workflows; minimal=20 cold-start curated CRUD; "
    "standard=109 DB-backed tools without legacy; full=113 including legacy "
    "agent tools. Counts enforced by tests/test_server_profiles.py.",
)
@click.option("--log-level", default=None, envvar="LOG_LEVEL")
@click.option("--log-format", default=None, envvar="LOG_FORMAT")
def main(
    transport: str,
    host: str,
    port: int,
    profile: str,
    log_level: str | None,
    log_format: str | None,
) -> None:
    """Run the COD-DOC MCP server."""
    setup_logging(level=log_level, fmt=log_format)
    stats = apply_profile(profile)
    _log.info(
        "mcp_profile_applied",
        extra={"event_type": "mcp_profile_applied", **stats},
    )
    if transport == "streamable-http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.settings.stateless_http = True
        mcp.run(transport="streamable-http")
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
