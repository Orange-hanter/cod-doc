"""Task list + detail handlers."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db, try_open_project_db
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import TaskStatus as LegacyTaskStatus
from cod_doc.domain.entities import EntityKind, TaskStatus
from cod_doc.services import plan_service as plans
from cod_doc.services import revision_service as revisions
from cod_doc.services import task_service as tasks

router = APIRouter()

LEGACY_TASK_STATUS_OPTIONS = [s.value for s in LegacyTaskStatus]
LEGACY_PAGE_SIZE_DEFAULT = 100
LEGACY_PAGE_SIZE_MAX = 500


@router.get("/p/{slug}/tasks", response_class=HTMLResponse)
def tasks_list(
    request: Request,
    slug: str,
    status: str | None = None,
) -> HTMLResponse:
    proj = get_project(slug)

    status_filter: TaskStatus | None = None
    status_invalid = False
    if status:
        try:
            status_filter = TaskStatus(status)
        except ValueError:
            status_invalid = True

    rows: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for t in tasks.list_for_project(session, project_db_id, status=status_filter):
                rows.append(
                    {
                        "task_id": t.task_id,
                        "title": t.title,
                        "status": t.status.value,
                        "type": t.type.value,
                        "priority": t.priority.value,
                        "plan_id": t.plan_id,
                        "section_id": t.section_id,
                    }
                )
    legacy_count = len(proj.get_tasks())
    return templates.TemplateResponse(
        request,
        "project/tasks_list.html",
        {
            "project": {"name": proj.entry.name},
            "tasks": rows,
            "db_available": db_available,
            "status_filter": status_filter.value if status_filter else "",
            "status_invalid": status_invalid,
            "legacy_count": legacy_count,
        },
    )


@router.get("/p/{slug}/tasks/legacy", response_class=HTMLResponse)
def tasks_legacy_list(
    request: Request,
    slug: str,
    status: str | None = None,
    limit: int = LEGACY_PAGE_SIZE_DEFAULT,
    offset: int = 0,
) -> HTMLResponse:
    """Render legacy YAML-stored tasks (.cod-doc/tasks.yaml) — paginated.

    These predate the DB schema and remain visible in the UI so importers
    and audits can see what is still un-migrated.
    """
    proj = get_project(slug)

    status_filter: LegacyTaskStatus | None = None
    status_invalid = False
    if status:
        try:
            status_filter = LegacyTaskStatus(status)
        except ValueError:
            status_invalid = True

    limit = max(1, min(limit, LEGACY_PAGE_SIZE_MAX))
    offset = max(0, offset)

    all_tasks = proj.get_tasks(status_filter)
    total = len(all_tasks)
    page = all_tasks[offset : offset + limit]

    rows: list[dict[str, Any]] = []
    for t in page:
        rows.append(
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority,
                "updated": t.updated,
            }
        )

    return templates.TemplateResponse(
        request,
        "project/tasks_legacy_list.html",
        {
            "project": {"name": proj.entry.name},
            "tasks": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "status_filter": status_filter.value if status_filter else "",
            "status_invalid": status_invalid,
            "legacy_status_options": LEGACY_TASK_STATUS_OPTIONS,
        },
    )


@router.get("/p/{slug}/tasks/{task_id}", response_class=HTMLResponse)
def task_show(
    request: Request,
    slug: str,
    task_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Task detail: header + description + acceptance + chains + revisions."""
    proj = get_project(slug)
    session, project_db_id = db

    task = tasks.get(session, task_id)
    if task is None or task.project_id != project_db_id:
        raise HTTPException(404, f"Задача не найдена: {task_id}")
    assert task.row_id is not None

    forward = plans.forward_chain(session, task_id)
    reverse = plans.reverse_chain(session, task_id)
    history = revisions.list_for_entity(session, EntityKind.TASK, task.row_id)

    # Plan + section breadcrumb info.
    plan = plans.get_for_project(session, project_db_id, task.plan_id)

    return templates.TemplateResponse(
        request,
        "project/task_show.html",
        {
            "project": {"name": proj.entry.name},
            "task": {
                "task_id": task.task_id,
                "title": task.title,
                "type": task.type.value,
                "status": task.status.value,
                "priority": task.priority.value,
                "description": task.description or "",
                "description_html": render_markdown(task.description or ""),
                "acceptance": task.acceptance or "",
                "acceptance_html": render_markdown(task.acceptance or ""),
                "plan_id": task.plan_id,
                "section_id": task.section_id,
                "created": task.created,
                "last_updated": task.last_updated,
                "completed_at": task.completed_at,
                "completed_commit": task.completed_commit,
            },
            "plan": (
                {"plan_id": plan.row_id, "scope": plan.scope}
                if plan and plan.row_id is not None
                else None
            ),
            "forward": [
                {
                    "task_id": e.task_id,
                    "title": e.title,
                    "status": e.status.value,
                    "depth": e.depth,
                }
                for e in forward
            ],
            "reverse": [
                {
                    "task_id": e.task_id,
                    "title": e.title,
                    "status": e.status.value,
                    "depth": e.depth,
                }
                for e in reverse
            ],
            "history": [
                {
                    "revision_id": r.revision_id,
                    "author": r.author,
                    "at": r.at,
                    "reason": r.reason or "",
                    "diff_first_line": (r.diff or "").splitlines()[0][:240]
                    if r.diff
                    else "",
                }
                for r in reversed(history)  # newest first for the timeline
            ],
        },
    )
