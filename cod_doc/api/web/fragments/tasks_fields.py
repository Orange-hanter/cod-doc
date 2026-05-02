"""Inline editing of free-text task fields (description / acceptance)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.errors import (
    ConflictWebError,
    NotFoundWebError,
    ValidationWebError,
)
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.services import task_service as tasks
from cod_doc.services.revision_service import RevisionConflictError

from ._shared import _is_htmx

router = APIRouter()


_TASK_FIELDS: dict[str, tuple[str, str, Callable[..., Any]]] = {
    "description": ("description", "Description", tasks.update_description),
    "acceptance": ("acceptance", "Acceptance criteria", tasks.update_acceptance),
}


def _render_task_field_view(
    request: Request, *, project_name: str, task: Any, field: str
) -> HTMLResponse:
    attr, label, _svc = _TASK_FIELDS[field]
    raw = getattr(task, attr) or ""
    html = templates.get_template("_frag/task_field_view.html").render(
        request=request,
        project={"name": project_name},
        task={"task_id": task.task_id},
        field=field,
        label=label,
        raw=raw,
        html=render_markdown(raw),
    )
    return HTMLResponse(html)


def _render_task_field_edit(
    request: Request, *, project_name: str, task: Any, field: str
) -> HTMLResponse:
    attr, label, _svc = _TASK_FIELDS[field]
    raw = getattr(task, attr) or ""
    html = templates.get_template("_frag/task_field_edit.html").render(
        request=request,
        project={"name": project_name},
        task={"task_id": task.task_id},
        field=field,
        label=label,
        raw=raw,
    )
    return HTMLResponse(html)


@router.get(
    "/p/{slug}/tasks/{task_id}/fields/{field}/edit",
    response_class=HTMLResponse,
)
def task_field_edit_form(
    request: Request,
    slug: str,
    task_id: str,
    field: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    if field not in _TASK_FIELDS:
        raise NotFoundWebError(f"Unknown task field: {field}")
    proj = get_project(slug)
    session, project_db_id = db
    task = tasks.get(session, task_id)
    if task is None or task.project_id != project_db_id:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")
    return _render_task_field_edit(
        request, project_name=proj.entry.name, task=task, field=field
    )


@router.get(
    "/p/{slug}/tasks/{task_id}/fields/{field}/view",
    response_class=HTMLResponse,
)
def task_field_view_fragment(
    request: Request,
    slug: str,
    task_id: str,
    field: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    if field not in _TASK_FIELDS:
        raise NotFoundWebError(f"Unknown task field: {field}")
    proj = get_project(slug)
    session, project_db_id = db
    task = tasks.get(session, task_id)
    if task is None or task.project_id != project_db_id:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")
    return _render_task_field_view(
        request, project_name=proj.entry.name, task=task, field=field
    )


@router.post(
    "/p/{slug}/tasks/{task_id}/fields/{field}",
    response_class=HTMLResponse,
)
def task_field_patch(
    request: Request,
    slug: str,
    task_id: str,
    field: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    body: str = Form(""),
) -> Response:
    if field not in _TASK_FIELDS:
        raise NotFoundWebError(f"Unknown task field: {field}")
    proj = get_project(slug)
    session, project_db_id = db
    existing = tasks.get(session, task_id)
    if existing is None or existing.project_id != project_db_id:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")

    _attr, _label, svc = _TASK_FIELDS[field]
    try:
        updated = svc(
            session,
            task_id=task_id,
            **{f"new_{field}": body},
            author="human:web",
            reason=f"web inline {field}",
        )
        session.commit()
    except RevisionConflictError as exc:
        session.rollback()
        raise ConflictWebError(f"Конфликт ревизий: {exc}") from exc
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        raise ValidationWebError(str(exc)) from exc

    if _is_htmx(request):
        return _render_task_field_view(
            request, project_name=proj.entry.name, task=updated, field=field
        )
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/tasks/{task_id}", status_code=303
    )
