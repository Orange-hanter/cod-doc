"""HTMX fragment endpoints — return small HTML chunks for swap targets.

Convention: each fragment endpoint accepts both HTMX and non-HTMX form posts.
- HTMX request (`HX-Request: true`) → returns the fragment HTML.
- Regular form post → 303 redirect back to the parent list page (so the user
  sees the new state without an empty <tr> being rendered as a full page).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError

from cod_doc.api.deps import get_project
from cod_doc.api.web.db_resolver import open_db_for_project
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import Task, TaskStatus
from cod_doc.services import task_service as tasks
from cod_doc.services.revision_service import RevisionConflictError

router = APIRouter()


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _render_task_row(
    request: Request,
    *,
    project_name: str,
    task: Any,
    error: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_frag/task_row.html",
        {
            "project": {"name": project_name},
            "t": {
                "task_id": task.task_id,
                "title": task.title,
                "type": task.type.value,
                "status": task.status.value,
                "priority": task.priority.value,
                "plan_id": task.plan_id,
                "section_id": task.section_id,
            },
            "status_options": [s.value for s in TaskStatus],
            "error": error,
        },
    )


@router.post("/p/{slug}/tasks/{task_id}/status", response_class=HTMLResponse)
def task_status_update(
    request: Request,
    slug: str,
    task_id: str,
    status: str = Form(...),
) -> Response:
    proj = get_project(slug)

    try:
        new_status = TaskStatus(status)
    except ValueError as exc:
        raise HTTPException(400, f"Неизвестное значение status: {status}") from exc

    with open_db_for_project(slug) as (session, project_db_id):
        if session is None or project_db_id is None:
            raise HTTPException(404, f"DB-проект ещё не инициализирован: {slug}")

        existing = tasks.get(session, task_id)
        if existing is None or existing.project_id != project_db_id:
            raise HTTPException(404, f"Задача не найдена: {task_id}")

        error: str | None = None
        try:
            updated = tasks.update_status(
                session,
                task_id=task_id,
                new_status=new_status,
                author="human:web",
                reason="web inline status",
            )
            session.commit()
        except RevisionConflictError as exc:
            session.rollback()
            updated = existing
            error = f"conflict: {exc}"
        except (IntegrityError, ValueError) as exc:
            session.rollback()
            updated = existing
            error = str(exc)

        if _is_htmx(request):
            return _render_task_row(
                request, project_name=proj.entry.name, task=updated, error=error
            )
        # Non-HTMX form post: redirect to the list page (303 See Other) so a
        # browser refresh doesn't re-submit the form.
        return RedirectResponse(
            url=f"/p/{proj.entry.name}/tasks", status_code=303
        )
