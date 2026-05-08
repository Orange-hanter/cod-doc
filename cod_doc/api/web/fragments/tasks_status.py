"""Task status update + completion handlers — share row+alert pipeline."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.errors import (
    NotFoundWebError,
    ValidationWebError,
    truncate_for_cookie,
)
from cod_doc.domain.entities import TaskStatus
from cod_doc.services import task_service as tasks
from cod_doc.services.revision_service import RevisionConflictError
from cod_doc.services.task_service import TaskAlreadyDoneError, TaskBlockedError

from ._shared import _is_htmx, _render_task_row

router = APIRouter()


@router.post("/p/{slug}/tasks/{task_id}/status", response_class=HTMLResponse)
def task_status_update(
    request: Request,
    slug: str,
    task_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    status: str = Form(...),
) -> Response:
    proj = get_project(slug)

    try:
        new_status = TaskStatus(status)
    except ValueError as exc:
        raise ValidationWebError(f"Неизвестное значение status: {status}") from exc

    session, project_db_id = db

    existing = tasks.get(session, task_id)
    if existing is None or existing.project_id != project_db_id:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")

    inline_alert: tuple[str, str] | None = None
    try:
        updated = tasks.update_status(
            session,
            task_id=task_id,
            new_status=new_status,
            author="human:web",
            reason="web inline status",
            strict=False,  # web UI allows direct jumps (user may skip steps)
        )
        session.commit()
    except RevisionConflictError as exc:
        session.rollback()
        updated = existing
        inline_alert = ("warning", f"conflict: {exc}")
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        updated = existing
        inline_alert = ("error", str(exc))

    if _is_htmx(request):
        return _render_task_row(
            request,
            project_name=proj.entry.name,
            task=updated,
            inline_alert=inline_alert,
        )
    # Non-HTMX form post: redirect to the list page (303 See Other) so a
    # browser refresh doesn't re-submit the form. Surface inline_alert via
    # cookie-flash when present.
    redirect = RedirectResponse(url=f"/p/{proj.entry.name}/tasks", status_code=303)
    if inline_alert is not None:
        severity, message = inline_alert
        redirect.set_cookie("flash_severity", severity, max_age=30, path="/")
        redirect.set_cookie(
            "flash_message",
            quote(truncate_for_cookie(message)),
            max_age=30,
            path="/",
        )
    return redirect


@router.post("/p/{slug}/tasks/{task_id}/complete", response_class=HTMLResponse)
def task_complete(
    request: Request,
    slug: str,
    task_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Mark a task done — endpoint for the Ready-block ✓ button (WEB-014).

    Errors get the same alert pipeline as `task_status_update`:
    - HTMX → returns the row plus an OOB alert,
    - Form post → 303 with cookie-flash.
    """
    proj = get_project(slug)
    session, project_db_id = db

    existing = tasks.get(session, task_id)
    if existing is None or existing.project_id != project_db_id:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")

    inline_alert: tuple[str, str] | None = None
    try:
        updated = tasks.complete(
            session,
            task_id=task_id,
            author="human:web",
            reason="web ready-block complete",
        )
        session.commit()
    except TaskAlreadyDoneError as exc:
        session.rollback()
        updated = existing
        inline_alert = ("info", f"already done: {exc}")
    except TaskBlockedError as exc:
        session.rollback()
        updated = existing
        inline_alert = ("warning", f"blocked: {exc}")
    except RevisionConflictError as exc:
        session.rollback()
        updated = existing
        inline_alert = ("warning", f"conflict: {exc}")
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        updated = existing
        inline_alert = ("error", str(exc))

    if _is_htmx(request):
        return _render_task_row(
            request,
            project_name=proj.entry.name,
            task=updated,
            inline_alert=inline_alert,
        )
    # Form-post: WEB-014b — return the user to where they came from when
    # possible (Referer), falling back to the project overview. The complete
    # button shows up on multiple pages (overview, plan view, tasks list)
    # and a hard-coded redirect to /p/{slug} was disorienting on the others.
    referer = request.headers.get("Referer") or f"/p/{proj.entry.name}"
    redirect = RedirectResponse(url=referer, status_code=303)
    if inline_alert is not None:
        severity, message = inline_alert
        redirect.set_cookie("flash_severity", severity, max_age=30, path="/")
        redirect.set_cookie(
            "flash_message",
            quote(truncate_for_cookie(message)),
            max_age=30,
            path="/",
        )
    return redirect
