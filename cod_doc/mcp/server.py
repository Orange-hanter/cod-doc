"""Native MCP server exposing COD-DOC project operations.

Provides a complete LLM interface to COD-DOC. Tool/resource/prompt
implementations live in `cod_doc.mcp.tools.*` modules; this file just
constructs the FastMCP instance, asks each module to register its
surface, and exposes a click-based entry point.
"""

from __future__ import annotations

import click
from mcp.server.fastmcp import FastMCP

from cod_doc.logging_config import setup_logging
from cod_doc.mcp.tools import (
    context_tools,
    doc_tools,
    legacy_agent_tools,
    legacy_master_tools,
    legacy_project_tools,
    legacy_resources,
    legacy_search_tools,
    link_tools,
    plan_tools,
    revision_tools,
    run_tools,
    skill_tools,
    story_tools,
    task_tools,
)

mcp = FastMCP("COD-DOC", json_response=True)

# Order doesn't matter for FastMCP — tool/resource/prompt names live in a
# flat namespace. Group registrations by surface for grep-ability.
for _module in (
    # Legacy YAML-backed surfaces (project mgmt, MASTER.md, agent, search).
    legacy_project_tools,
    legacy_master_tools,
    legacy_search_tools,
    legacy_agent_tools,
    legacy_resources,
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
):
    _module.register(mcp)


@click.command()
@click.option("--transport", type=click.Choice(["stdio", "streamable-http"]), default="stdio")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, type=int, show_default=True)
@click.option("--log-level", default=None, envvar="LOG_LEVEL")
@click.option("--log-format", default=None, envvar="LOG_FORMAT")
def main(
    transport: str, host: str, port: int, log_level: str | None, log_format: str | None
) -> None:
    """Run the COD-DOC MCP server."""
    setup_logging(level=log_level, fmt=log_format)
    if transport == "streamable-http":
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.settings.stateless_http = True
        mcp.run(transport="streamable-http")
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
