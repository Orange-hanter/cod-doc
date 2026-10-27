"""MCP tool: ``roadmap_get`` — очередь работ проекта и прогноз (RFC 36 §3.6, ROAD-005).

Профили ``standard`` / ``full``; ``agent`` и ``minimal`` — явные allowlist'ы
в ``cod_doc/mcp/profiles.py``, имени там нет (контракт C-03). Тул чтения:
внесён в ``mcp/access_policy.READER_TOOLS``. Ничего не пишет (C-11).

Перестановка планов — ``plan_move`` (семейство ``plan.*``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    @mcp.tool(name="roadmap_get")
    def roadmap_get(
        project: str,
        include_backlog: bool = False,
        weeks: int = 8,
        plan_scope: str | None = None,
        seed: int = 0,
        asof: str | None = None,
    ) -> dict[str, Any]:
        """Roadmap of the project: work order and a throughput forecast (RFC 36).

        Order is the contract: plans by ``plan.rank`` (``plan_move``), then
        section, priority, task_id; a blocker is pulled ahead of every task it
        blocks, across plans (``pulled_by``). Dates are NOT deadlines: ``p50``
        / ``p85`` say when the queue is expected to reach a task, from the
        project's closed-per-week history over ``weeks`` full weeks
        (Monte-Carlo, ``seed``). ``forecast.warnings`` lists what makes the
        dates unreliable (``inflow_exceeds``, ``short_sample``,
        ``zero_sample``, ``beyond_horizon``).

        ``plan_scope`` filters the returned rows and plans, not the queue:
        positions and dates stay those of the whole project. ``backlog`` tasks
        join only with ``include_backlog=true``. A dependency cycle raises
        ``ValueError`` naming the cycles — there is no partial queue.
        ``asof`` (``YYYY-MM-DD``, default today UTC) fixes the forecast origin:
        with the same ``asof`` and ``seed`` the answer equals ``cod-doc roadmap
        show --json`` and ``GET /api/v1/projects/{slug}/roadmap``.

        Returns ``{meaning, queue: {rows, plans}, forecast: {asof, sample,
        plans, warnings, runs, seed}}``; every row carries ``p50``/``p85``.
        """
        from datetime import UTC, date, datetime

        from cod_doc.infra.db import transactional
        from cod_doc.services import roadmap_service
        from cod_doc.services.serializers import roadmap_to_dict

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            roadmap = roadmap_service.build(
                session,
                project_id=project_id,
                asof=date.fromisoformat(asof) if asof else datetime.now(UTC).date(),
                include_backlog=include_backlog,
                weeks=weeks,
                seed=seed,
            )
        return roadmap_to_dict(roadmap, plan_scope=plan_scope)
