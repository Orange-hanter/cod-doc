"""MCP tools: search — FTS5 corpus (not ``tool_search``, not ``ctx_search``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

SEARCH_DEFAULT_LIMIT = 20


def register(mcp: FastMCP) -> None:
    """Register the ``search`` tool on the given FastMCP instance."""

    @mcp.tool(name="search")
    def search(
        project: str,
        query: str,
        scope: str | None = None,
        limit: int = SEARCH_DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """FTS5 search over tasks / docs / stories / ADRs / findings.

        ``scope`` ∈ ``{task, doc, story, adr, finding}``. There is no reindex
        flag — rebuild with ``cod-doc search --reindex`` / ``cod-doc reindex``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import search_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return search_service.search(
                session,
                project_id=project_id,
                query=query,
                scope=scope,
                limit=limit,
            )
