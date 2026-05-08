"""MCP tools: activity.* — unified audit timeline (PCA-111, proposal 09).

Tools
-----
- ``activity_list``       — paginated event stream with rich filters
- ``activity_for_run``    — all events emitted during one agent run
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register activity.* tools on the given FastMCP instance."""

    @mcp.tool(name="activity_list")
    def activity_list(
        project: str,
        scope_kind: str | None = None,
        scope_id: str | None = None,
        kind: str | None = None,
        actor_kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List activity events, newest first.

        scope_kind: 'task' | 'doc' | 'task_doc' | 'story' | 'project' | 'approval' | 'run'
        scope_id: task_id / doc_key / story_id / ...
        kind: canonical event kind (e.g. 'task.status_changed', 'doc.updated')
        actor_kind: 'orchestrator' | 'human' | 'routine' | 'system'
        since / until: ISO-8601 datetime strings (UTC).
        """
        from datetime import datetime

        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service

        since_dt = datetime.fromisoformat(since) if since else None
        until_dt = datetime.fromisoformat(until) if until else None

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            result = activity_service.list_events(
                session,
                project_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                kind=kind,
                actor_kind=actor_kind,
                since=since_dt,
                until=until_dt,
                limit=limit,
                offset=offset,
            )
        return result

    @mcp.tool(name="activity_for_run")
    def activity_for_run(
        project: str,
        run_id: str,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """All activity events emitted during a specific agent run, oldest first."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            events = activity_service.events_for_run(session, project_id, run_id, limit=limit)
        return events
