"""GET / — project card grid with full task stats."""

from __future__ import annotations

from typing import Any

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

    # DB-проход первый: только он решает, нужен ли вообще legacy-`tasks.yaml`.
    # Разбор этого файла стоит десятки миллисекунд на проект, а на DB-проекте
    # все его счётчики всё равно перекрываются агрегатами из БД (total=0 из БД
    # означает «проект ещё на tasks.yaml» — вот там fallback и нужен).
    db_stats_by_name: dict[str, dict[str, Any]] = {}
    for entry in page_entries:
        with try_open_project_db(entry.name) as (session, project_db_id):
            if session is None or project_db_id is None:
                continue
            db_stats = _normalize_stats(task_service.summarize_for_project(session, project_db_id))
        if db_stats["total"] > 0:
            db_stats_by_name[entry.name] = db_stats

    yaml_entries = [e for e in page_entries if e.name not in db_stats_by_name]
    yaml_stats = dict(
        zip(
            (e.name for e in yaml_entries),
            Project.batch_stats(yaml_entries),
            strict=True,
        )
    )

    projects = []
    for entry in page_entries:
        db_row = db_stats_by_name.get(entry.name)
        stats: dict[str, Any]
        if db_row is not None:
            stats = {**db_row, **Project(entry).run_state()}
        else:
            stats = dict(yaml_stats[entry.name])
            stats.setdefault(
                "pct", round(stats["done"] / stats["total"] * 100) if stats.get("total") else 0
            )
            stats.setdefault("pending", 0)
            stats.setdefault("failed", 0)

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
