"""Task status update + completion handlers — share row+alert pipeline."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.errors import (
    NotFoundWebError,
    ValidationWebError,
    _safe_back_url,
    set_flash_cookie,
)
from cod_doc.domain.entities import TaskStatus
from cod_doc.services import checkout_service
from cod_doc.services import task_service as tasks
from cod_doc.services.checkout_service import CheckoutConflictError, CheckoutStatusError
from cod_doc.services.revision_service import RevisionConflictError
from cod_doc.services.sync_service.errors import SyncError
from cod_doc.services.task_service import TaskAlreadyDoneError, TaskBlockedError
from cod_doc.services.task_status_machine import StatusTransitionError, normalise

from ._shared import _is_htmx, _log_write_failure, _render_task_row

router = APIRouter()
logger = logging.getLogger("cod_doc.api")

# Текст для баннера, когда сбой не доменный. str() от IntegrityError несёт
# SQL и параметры запроса — схему БД пользователю показывать незачем; в лог
# сервера уходят тип исключения и имя ограничения.
_GENERIC_SAVE_ERROR = "Не удалось сохранить изменения."


def _generic_save_error(exc: Exception, *, task_id: str) -> tuple[str, str]:
    _log_write_failure(logger, "web task write", task_id, exc)
    return ("error", _GENERIC_SAVE_ERROR)


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

    existing = tasks.get(session, task_id, project_id=project_db_id)
    if existing is None:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")

    inline_alert: tuple[str, str] | None = None
    try:
        # ADO-039 (Phase-2 enforce): todo→in_progress — только через checkout,
        # даже на web. Остальные прямые прыжки — по-прежнему strict=False.
        if (
            normalise(existing.status.value) == "todo"
            and normalise(new_status.value) == "in_progress"
        ):
            checkout_service.checkout(
                session, task_id=task_id, project_id=project_db_id, agent="human:web"
            )
            checked = tasks.get(session, task_id, project_id=project_db_id)
            assert checked is not None
            updated = checked
        else:
            updated = tasks.update_status(
                session,
                project_id=project_db_id,
                task_id=task_id,
                new_status=new_status,
                author="human:web",
                reason="web inline status",
                strict=False,  # web UI allows direct jumps (user may skip steps)
            )
        session.commit()
    except (CheckoutConflictError, CheckoutStatusError, SyncError) as exc:
        # SyncError — аренда замка на remote (SYNC-109/110, CLO-031).
        session.rollback()
        updated = existing
        inline_alert = ("error", str(exc))
    except RevisionConflictError as exc:
        session.rollback()
        updated = existing
        inline_alert = ("warning", f"conflict: {exc}")
    except StatusTransitionError as exc:
        # Доменный отказ статус-машины: текст наш и осмысленный.
        session.rollback()
        updated = existing
        inline_alert = ("error", str(exc))
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        updated = existing
        inline_alert = _generic_save_error(exc, task_id=task_id)

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
        set_flash_cookie(redirect, request, severity=severity, message=message)
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

    existing = tasks.get(session, task_id, project_id=project_db_id)
    if existing is None:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")

    inline_alert: tuple[str, str] | None = None
    try:
        updated = tasks.complete(
            session,
            project_id=project_db_id,
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
    except StatusTransitionError as exc:
        # Доменный отказ статус-машины: текст наш и осмысленный.
        session.rollback()
        updated = existing
        inline_alert = ("error", str(exc))
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        updated = existing
        inline_alert = _generic_save_error(exc, task_id=task_id)

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
    back = _safe_back_url(request, f"/p/{proj.entry.name}")
    redirect = RedirectResponse(url=back, status_code=303)
    if inline_alert is not None:
        severity, message = inline_alert
        set_flash_cookie(redirect, request, severity=severity, message=message)
    return redirect
