"""GET / — registry of projects with paginated KPI summaries."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import daemon_is_running, get_config, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import Project
from cod_doc.services import plan_service as plans

router = APIRouter()

INDEX_DEFAULT_LIMIT = 20
INDEX_MAX_LIMIT = 200


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    limit: int = INDEX_DEFAULT_LIMIT,
    offset: int = 0,
) -> HTMLResponse:
    cfg = get_config()
    all_entries = cfg.list_projects()
    total = len(all_entries)
    # Clamp to defensive bounds — page sizes are user-supplied query params.
    limit = max(1, min(limit, INDEX_MAX_LIMIT))
    offset = max(0, offset)
    page_entries = all_entries[offset : offset + limit]

    # Prefer DB-aggregated plan stats; fall back to YAML stats if DB not ready.
    yaml_stats = Project.batch_stats(page_entries)
    projects = []
    for entry, fallback in zip(page_entries, yaml_stats, strict=True):
        stats = fallback
        with try_open_project_db(entry.name) as (session, project_db_id):
            if session is not None and project_db_id is not None:
                project_plans = plans.list_for_project(session, project_db_id)
                if project_plans:
                    total_tasks = done_tasks = in_progress_tasks = 0
                    for plan in project_plans:
                        assert plan.row_id is not None
                        p = plans.recalc(session, plan.row_id)
                        total_tasks += p.total
                        done_tasks += p.done
                        in_progress_tasks += p.in_progress
                    stats = {
                        **fallback,
                        "total": total_tasks,
                        "done": done_tasks,
                        "in_progress": in_progress_tasks,
                    }
        projects.append(
            {
                "name": entry.name,
                "path": entry.path,
                "enabled": entry.enabled,
                "daemon_enabled": entry.daemon_enabled,
                "stats": stats,
            }
        )

    has_prev = offset > 0
    has_next = offset + limit < total
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit

    # Empty page (offset >= total OR no projects at all) → show "0–0 of N"
    # rather than a backwards range like "11–10 of 10".
    if projects:
        showing_from = offset + 1
        showing_to = offset + len(projects)
    else:
        showing_from = 0
        showing_to = 0

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "projects": projects,
            "configured": cfg.is_configured,
            "daemon_running": daemon_is_running(),
            "agent_enabled": cfg.agent_enabled,
            "page": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_prev": has_prev,
                "has_next": has_next,
                "prev_offset": prev_offset,
                "next_offset": next_offset,
                "showing_from": showing_from,
                "showing_to": showing_to,
            },
        },
    )
