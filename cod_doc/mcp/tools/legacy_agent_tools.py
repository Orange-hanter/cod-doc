"""Agent orchestration + global config inspection tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._legacy import load_config, open_project

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register agent + config tools."""

    @mcp.tool()
    async def run_agent_once(
        project_name: str, autonomous: bool = True
    ) -> list[dict[str, Any]]:
        """Run the COD-DOC agent once: autonomous or single-task mode."""
        from cod_doc.agent.orchestrator import Orchestrator

        cfg = load_config()
        if not cfg.is_configured:
            raise ValueError("API-ключ не настроен. Запустите cod-doc wizard")

        proj = open_project(project_name)
        orch = Orchestrator(proj, cfg)
        events: list[dict[str, Any]] = []

        if autonomous:
            gen = orch.run_autonomous()
        else:
            task = proj.next_pending_task()
            if not task:
                return []
            gen = orch.run_task(task)

        async for event in gen:
            events.append(event.to_dict())
        return events

    @mcp.tool()
    def get_agent_context(project_name: str) -> list[dict[str, str]]:
        """Return the agent's conversation history (last 50 messages) for a project."""
        proj = open_project(project_name)
        return proj.get_context_messages()

    @mcp.tool()
    def clear_agent_context(project_name: str) -> dict[str, str]:
        """Clear the agent's conversation history for a project."""
        proj = open_project(project_name)
        proj.clear_context()
        return {"cleared": project_name}

    @mcp.tool()
    def check_config() -> dict[str, Any]:
        """Check COD-DOC configuration status."""
        cfg = load_config()
        return {
            "is_configured": cfg.is_configured,
            "project_count": len(cfg.list_projects()),
            "api_host": cfg.api_host,
            "api_port": cfg.api_port,
        }
