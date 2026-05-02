"""MCP resources + prompts for legacy YAML-backed surfaces."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ._legacy import load_config, open_project, project_summary

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register resources + prompts."""

    @mcp.resource("cod-doc://config")
    def config_resource() -> str:
        """COD-DOC configuration (sanitized, no API key)."""
        cfg = load_config().model_dump()
        cfg.pop("api_key", None)
        return json.dumps(cfg, ensure_ascii=False, indent=2)

    @mcp.resource("cod-doc://projects")
    def projects_resource() -> str:
        """Registry of all COD-DOC projects with stats."""
        cfg = load_config()
        return json.dumps(
            [project_summary(entry) for entry in cfg.list_projects()],
            ensure_ascii=False,
            indent=2,
        )

    @mcp.resource("cod-doc://project/{project_name}/master")
    def project_master_resource(project_name: str) -> str:
        """MASTER.md content for a specific project."""
        proj = open_project(project_name)
        content = proj.read_master()
        if content is None:
            raise ValueError(f"MASTER.md не найден для проекта: {project_name}")
        return content

    @mcp.resource("cod-doc://project/{project_name}/tasks")
    def project_tasks_resource(project_name: str) -> str:
        """Task list for a specific project."""
        proj = open_project(project_name)
        return json.dumps(
            [t.to_dict() for t in proj.get_tasks()], ensure_ascii=False, indent=2
        )

    @mcp.prompt()
    def doc_review(project_name: str, focus: str = "structure and stale links") -> str:
        """Review project documentation: identify missing modules, stale hashes, next actions."""
        return (
            f"Review the COD-DOC documentation for project '{project_name}'. "
            f"Focus on {focus}. "
            "Use the project status, task list, and MASTER.md to identify missing modules, "
            "stale hashes, and the next concrete documentation actions."
        )

    @mcp.prompt()
    def doc_plan(project_name: str) -> str:
        """Generate a documentation plan for a project from scratch."""
        return (
            f"Create a comprehensive documentation plan for the project '{project_name}'. "
            "Steps:\n"
            "1. Use list_files to discover the project structure\n"
            "2. Read key source files to understand the architecture\n"
            "3. Check the current MASTER.md state\n"
            "4. Identify all documentation levels needed (L0 overview, L1 modules, L2 implementation)\n"
            "5. Create tasks for each documentation unit\n"
            "6. Prioritize: architecture overview first, then API/interface docs, then implementation"
        )

    @mcp.prompt()
    def onboard_project(project_name: str) -> str:
        """Onboard a new project into COD-DOC: scan, plan docs, create initial tasks."""
        return (
            f"Onboard the project '{project_name}' into COD-DOC documentation system. "
            "Workflow:\n"
            "1. Use get_project_status to check current state\n"
            "2. Use list_files to discover project structure\n"
            "3. Read MASTER.md to understand what's already documented\n"
            "4. Use check_stale_refs to find broken or outdated references\n"
            "5. Create documentation tasks covering all undocumented areas\n"
            "6. Run update_master_hashes to fix stale hashes\n"
            "7. Provide a summary of the documentation coverage and gaps"
        )
