"""Project overview + DB-init handler."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cod_doc.api.deps import get_project, try_open_project_db
from cod_doc.api.web.errors import truncate_for_cookie
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import EntityKind
from cod_doc.services import plan_service as plans
from cod_doc.services import project_service as projects
from cod_doc.services import revision_service as revisions

from ._helpers import MASTER_PREVIEW_LINES, _preview

router = APIRouter()

OVERVIEW_READY_LIMIT = 5
OVERVIEW_REVISIONS_LIMIT = 5


@router.get("/p/{slug}", response_class=HTMLResponse)
def project_show(request: Request, slug: str) -> HTMLResponse:
    proj = get_project(slug)
    master = proj.read_master()
    master_preview, master_truncated = _preview(master, MASTER_PREVIEW_LINES)
    master_html = render_markdown(master_preview or "") or None

    # WEB-014 — overview aggregator: ready-to-start tasks, plan-progress
    # mini-bars, recent revisions. Each block is independent and is left
    # empty (not crashed) if the DB project isn't initialised yet.
    ready_tasks: list[dict[str, Any]] = []
    plan_rows: list[dict[str, Any]] = []
    recent_revs: list[dict[str, Any]] = []
    db_available = False

    # Header KPI cards: prefer DB-aggregated totals (single source of truth
    # with the Plan-progress block below). Fall back to legacy YAML stats
    # when the DB isn't initialised — same shape so the template doesn't
    # need to branch.
    yaml_stats: dict[str, Any] = proj.stats()
    db_total = db_done = db_in_progress = 0

    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            project_plans = plans.list_for_project(session, project_db_id)
            for plan in project_plans:
                assert plan.row_id is not None
                progress = plans.recalc(session, plan.row_id)
                db_total += progress.total
                db_done += progress.done
                db_in_progress += progress.in_progress
                plan_rows.append(
                    {
                        "plan_id": plan.row_id,
                        "scope": plan.scope,
                        "total": progress.total,
                        "done": progress.done,
                        "in_progress": progress.in_progress,
                        "remaining": progress.remaining,
                        "status": progress.status.value,
                        "percent": (
                            round(100 * progress.done / progress.total)
                            if progress.total
                            else 0
                        ),
                    }
                )
                # Top-N ready-to-start across all plans, capped overall.
                if len(ready_tasks) < OVERVIEW_READY_LIMIT:
                    for t in plans.ready(
                        session, plan.row_id, limit=OVERVIEW_READY_LIMIT - len(ready_tasks)
                    ):
                        ready_tasks.append(
                            {
                                "task_id": t.task_id,
                                "title": t.title,
                                "type": t.type.value,
                                "status": t.status.value,
                                "priority": t.priority.value,
                                "plan_id": t.plan_id,
                                "section_id": t.section_id,
                            }
                        )

            for r in revisions.list_recent_for_project(
                session, project_db_id, limit=OVERVIEW_REVISIONS_LIMIT
            ):
                recent_revs.append(
                    {
                        "revision_id": r.revision_id,
                        "entity_kind": r.entity_kind.value
                        if isinstance(r.entity_kind, EntityKind)
                        else str(r.entity_kind),
                        "entity_id": r.entity_id,
                        "author": r.author,
                        "at": r.at,
                        "reason": r.reason or "",
                    }
                )

    if db_available and any((db_total, db_done, db_in_progress)):
        kpi = {
            **yaml_stats,
            "total": db_total,
            "done": db_done,
            "in_progress": db_in_progress,
            "pending": db_total - db_done - db_in_progress,
        }
    else:
        kpi = yaml_stats

    return templates.TemplateResponse(
        request,
        "project/show.html",
        {
            "project": {
                "name": proj.entry.name,
                "path": proj.entry.path,
                "enabled": proj.entry.enabled,
                "master_md": proj.entry.master_md,
                "master_exists": proj.entry.master_path.exists(),
            },
            "stats": kpi,
            "master_preview": master_preview,
            "master_html": master_html,
            "master_truncated": master_truncated,
            "db_available": db_available,
            "ready_tasks": ready_tasks,
            "plan_rows": plan_rows,
            "recent_revs": recent_revs,
        },
    )


@router.post("/p/{slug}/init", response_class=HTMLResponse)
def project_init_db(request: Request, slug: str) -> Response:
    """Bootstrap the project's `.cod-doc/state.db` (alembic + ProjectModel row).

    Idempotent — calling on an already-initialised project is a no-op aside
    from a confirmation flash. After init, the engine cache is invalidated
    so subsequent reads see the freshly-migrated schema immediately.
    """
    proj = get_project(slug)
    result = projects.init_project(proj.entry)

    # Engine cache invalidation: drop the (slug → engine) cache so the next
    # request reopens against the migrated DB.
    from cod_doc.api.deps import dispose_all_engines

    dispose_all_engines()

    if result.db_row_existed:
        message = f"Проект «{slug}»: БД уже была инициализирована."
        severity = "info"
    elif result.db_existed:
        message = f"Проект «{slug}»: миграции применены, project-row создан."
        severity = "info"
    else:
        message = f"Проект «{slug}» инициализирован. Миграции применены, project-row создан."
        severity = "info"

    redirect = RedirectResponse(url=f"/p/{slug}", status_code=303)
    redirect.set_cookie("flash_severity", severity, max_age=30, path="/")
    redirect.set_cookie(
        "flash_message",
        quote(truncate_for_cookie(message)),
        max_age=30,
        path="/",
    )
    return redirect
