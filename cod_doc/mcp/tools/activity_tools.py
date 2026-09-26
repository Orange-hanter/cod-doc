"""MCP tools: activity.* — unified audit timeline (PCA-111, proposal 09).

Tools
-----
- ``activity_list``       — paginated event stream with rich filters
- ``activity_summary``    — GROUP BY агрегаты (RFC 27 F9): день / actor_kind /
  kind / scope_kind одним вызовом вместо прямого SQL

ADR-012 (ADO-044): тул ``activity_for_run`` удалён — `run_id` пуст на
всех 1114 событиях живой БД, потому что run-скоуп открывает только
встроенный раннер. Сервисная функция ``activity_service.events_for_run``
сохранена: её зовёт web-консоль ``/p/{slug}/run``.
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
        actor_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List activity events, newest first.

        scope_kind: 'task' | 'doc' | 'task_doc' | 'story' | 'project' | 'approval' | 'run'
        scope_id: task_id / doc_key / story_id / ...
        kind: canonical event kind (e.g. 'task.status_changed', 'doc.updated')
        actor_kind: см. ``domain.entities.ActorKind`` — 'human' | 'agent' |
            'orchestrator' | 'routine' | 'system' | 'cli' | 'api'
        actor_id: канонический ``<kind>:<id>`` (``human:dakh``,
            ``agent:claude-opus-5``) — точный фильтр по актору.
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
            return activity_service.list_events(
                session,
                project_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                kind=kind,
                actor_kind=actor_kind,
                actor_id=actor_id,
                since=since_dt,
                until=until_dt,
                limit=limit,
                offset=offset,
            )

    @mcp.tool(name="activity_summary")
    def activity_summary(
        project: str,
        since: str,
        until: str | None = None,
        group_by: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate activity events with a single GROUP BY query (RFC 27 F9).

        Заменяет прямой SQL «события за N дней по дням и actor_kind»:
        ``activity_summary(project, since='2026-09-12',
        group_by=['day', 'actor_kind'])``.

        group_by: ключи агрегации — 'day' | 'actor_kind' | 'kind' |
            'scope_kind'; None → ['day']. Недопустимый ключ — ошибка со
            списком допустимых.
        since / until: ISO-8601 (bare date = полночь UTC), until
            включительно; None → без верхней границы.

        Возвращает ``[{…ключи группы, "n": count}]`` — по одной строке на
        комбинацию ключей; сумма ``n`` равна ``activity_list(...).total``
        за тот же период.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, task_service

        since_dt = task_service.parse_since(since)
        until_dt = task_service.parse_since(until) if until else None
        keys = group_by if group_by is not None else ["day"]

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return activity_service.summarize(
                session,
                project_id,
                since=since_dt,
                until=until_dt,
                group_by=keys,
            )
