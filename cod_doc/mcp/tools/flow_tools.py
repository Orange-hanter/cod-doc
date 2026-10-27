"""MCP tool: ``flow_metrics_rebuild`` — пересчёт метрик длительности (RFC 33 §3.1, FLW-003).

Профили ``standard`` / ``full``; ``agent`` и ``minimal`` — явные allowlist'ы
в ``cod_doc/mcp/profiles.py``, имени там нет. Тул пишущий: в
``mcp/access_policy.READER_TOOLS`` его нет. Зеркало CLI — ``cod-doc flow
rebuild-metrics``.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="flow_metrics_rebuild")
    def flow_metrics_rebuild(
        project: str,
        author: str = "mcp",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Recompute ``task_metrics`` of every closed task from the transition journal.

        Time in ``in_progress`` / ``blocked`` is rebuilt from ``activity_event``
        (``task.checked_out``, ``task.status_changed`` with ``old_status``,
        ``task.completed``); events without ``old_status`` — old MCP duplicates —
        are ignored. Idempotent; missing rows are created. Writes one
        ``metrics.rebuilt`` event with the counters; ``dry_run=true`` only
        reports.

        Returns ``{tasks, created, updated, with_in_progress, without_events,
        dry_run}`` — ``without_events`` are tasks older than the journal.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import metrics_service

        sf, _ = session_factory(project)
        with transactional(sf, commit=not dry_run) as session:
            report = metrics_service.rebuild(
                session,
                project_id=require_project_id(session, project),
                author=author,
                dry_run=dry_run,
            )
        return {**dataclasses.asdict(report), "dry_run": dry_run}
