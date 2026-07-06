"""HTMX fragments for live task board refresh (WebSocket → kanban swap)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import get_project, try_open_project_db
from cod_doc.api.web.task_board import (
    board_refresh_url,
    build_columns,
    compute_stats,
    load_task_rows,
)
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import TaskStatus

router = APIRouter()


@router.get("/p/{slug}/frag/tasks/board", response_class=HTMLResponse)
def tasks_board_fragment(
    request: Request,
    slug: str,
    plan: str = "",
    status: str = "",
) -> HTMLResponse:
    """Return the stats strip + kanban region for HTMX outerHTML swap.

    Query params mirror the tasks list page filters so live refresh preserves
    the user's plan/status context.
    """
    proj = get_project(slug)

    status_filter: TaskStatus | None = None
    if status:
        try:
            status_filter = TaskStatus(status)
        except ValueError:
            status_filter = None

    rows: list[dict[str, Any]] = []
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            rows = load_task_rows(session, project_db_id)

    if plan:
        rows = [r for r in rows if r["plan_scope"] == plan]

    stats = compute_stats(rows)
    columns = build_columns(rows, status_filter=status_filter)

    return templates.TemplateResponse(
        request,
        "_frag/tasks_live_region.html",
        {
            "project": {"name": proj.entry.name},
            "stats": stats,
            "columns": columns,
            "selected_plan": plan,
            "board_refresh_url": board_refresh_url(proj.entry.name, plan=plan, status=status),
        },
    )
