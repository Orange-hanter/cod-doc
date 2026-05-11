"""GET / — project card grid with full task stats."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import daemon_is_running, get_config, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import Project
from cod_doc.services import task_service

router = APIRouter()

INDEX_DEFAULT_LIMIT = 20
INDEX_MAX_LIMIT = 200


def _normalize_stats(raw: dict) -> dict:  # type: ignore[type-arg]
    """Convert task_service.summarize_for_project output to the flat shape used in templates."""
    by_status = raw.get("by_status", {})
    pending = by_status.get("pending", 0)
    in_progress = by_status.get("in-progress", by_status.get("in_progress", 0))
    done = by_status.get("done", 0)
    failed = by_status.get("failed", 0)
    total = raw.get("total", pending + in_progress + done + failed)
    pct = round(done / total * 100) if total else 0
    return {
        "total": total,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "failed": failed,
        "pct": pct,
        "status": None,
        "last_run": None,
    }


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    limit: int = INDEX_DEFAULT_LIMIT,
    offset: int = 0,
) -> HTMLResponse:
    cfg = get_config()
    all_entries = cfg.list_projects()
    total = len(all_entries)
    limit = max(1, min(limit, INDEX_MAX_LIMIT))
    offset = max(0, offset)
    page_entries = all_entries[offset : offset + limit]

    yaml_stats = Project.batch_stats(page_entries)
    projects = []
    for entry, fallback in zip(page_entries, yaml_stats, strict=True):
        stats = dict(fallback)
        stats.setdefault("pct", round(stats["done"] / stats["total"] * 100) if stats.get("total") else 0)
        stats.setdefault("pending", 0)
        stats.setdefault("failed", 0)

        with try_open_project_db(entry.name) as (session, project_db_id):
            if session is not None and project_db_id is not None:
                raw = task_service.summarize_for_project(session, project_db_id)
                db_stats = _normalize_stats(raw)
                # Only replace YAML stats when DB actually has tasks.
                # Projects still using legacy tasks.yaml return total=0 from DB.
                if db_stats["total"] > 0:
                    stats.update({
                        "total": db_stats["total"],
                        "pending": db_stats["pending"],
                        "in_progress": db_stats["in_progress"],
                        "done": db_stats["done"],
                        "failed": db_stats["failed"],
                        "pct": db_stats["pct"],
                    })

        projects.append({
            "name": entry.name,
            "path": entry.path,
            "enabled": entry.enabled,
            "daemon_enabled": entry.daemon_enabled,
            "stats": stats,
        })

    has_prev = offset > 0
    has_next = offset + limit < total
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit

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
