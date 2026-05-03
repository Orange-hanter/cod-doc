"""MCP tool: context.get — assemble project context for agents (COD-033)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register context tools."""

    @mcp.tool()
    def context_get(
        project: str,
        target_kind: str,
        target_id: str,
        depth: str = "L1",
        token_budget: int = 8000,
    ) -> dict[str, Any]:
        """Assemble minimal-sufficient context for the given target.

        Parameters
        ----------
        project:     Project slug.
        target_kind: One of ``document``, ``task``, ``plan``, ``module``.
        target_id:   Identifier within the target kind:
                     - document → doc_key (e.g. ``modules/M1-auth/overview``)
                     - task     → task_id (e.g. ``AUTH-025``)
                     - plan     → plan scope (e.g. ``M1-auth-module``)
                     - module   → module_id (e.g. ``M1-auth``)
        depth:       ``L0`` (metadata only) | ``L1`` (body + direct relations).
                     L2/L3 reserved for future semantic expansion.
        token_budget: Approximate token ceiling (default 8000).
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import context_service

        SessionFactory, entry = session_factory(project)
        with transactional(SessionFactory) as session:
            project_id = require_project_id(session, project)

            # Optionally attach master_content for master_excerpt
            master_content: str | None = None
            try:
                if entry.master_path.exists():
                    master_content = entry.master_path.read_text(encoding="utf-8")
            except Exception:
                pass

            return context_service.context_get(
                session=session,
                project_id=project_id,
                target_kind=target_kind,
                target_id=target_id,
                depth=depth,
                token_budget=token_budget,
                master_content=master_content,
            )
