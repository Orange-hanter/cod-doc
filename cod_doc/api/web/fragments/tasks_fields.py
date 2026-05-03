"""Inline editing of free-text task fields (description / acceptance)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config, get_project, get_project_db
from cod_doc.api.web.errors import (
    ConflictWebError,
    NotFoundWebError,
    ValidationWebError,
)
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.services import ai_text
from cod_doc.services import task_service as tasks
from cod_doc.services import trace_service
from cod_doc.services.ai_text import AIBackendError
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
    request: Request,
    *,
    project_name: str,
    task: Any,
    field: str,
    raw_override: str | None = None,
    intent: str = "",
    notice: str = "",
) -> HTMLResponse:
    """Render the edit fragment.

    ``raw_override`` lets the AI-improve flow swap in suggested text while
    keeping the user's intent in the input box. ``notice`` carries an inline
    success/error string (e.g. "AI suggestion ready" / "LLM error: …").
    """
    attr, label, _svc = _TASK_FIELDS[field]
    raw = raw_override if raw_override is not None else (getattr(task, attr) or "")
    html = templates.get_template("_frag/task_field_edit.html").render(
        request=request,
        project={"name": project_name},
        task={"task_id": task.task_id},
        field=field,
        label=label,
        raw=raw,
        intent=intent,
        notice=notice,
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
    "/p/{slug}/tasks/{task_id}/fields/{field}/improve",
    response_class=HTMLResponse,
)
def task_field_improve(
    request: Request,
    slug: str,
    task_id: str,
    field: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    body: str = Form(""),
    intent: str = Form(""),
) -> Response:
    """Run the current draft through the LLM "improve" pass and return
    a refreshed edit fragment with the suggestion swapped into the textarea.

    The DB is NOT touched — the user can still hit Save (which writes the
    suggestion) or keep editing.
    """
    if field not in _TASK_FIELDS:
        raise NotFoundWebError(f"Unknown task field: {field}")
    proj = get_project(slug)
    session, project_db_id = db
    task = tasks.get(session, task_id)
    if task is None or task.project_id != project_db_id:
        raise NotFoundWebError(f"Задача не найдена: {task_id}")

    cfg = get_config()
    assert task.row_id is not None
    try:
        result = ai_text.improve_text_traced(body, intent, cfg=cfg)
        improved = result.text
        notice = "AI suggestion ready — review, then Save to apply."
        trace_service.record(
            session,
            model=result.model,
            task_id=task.row_id,
            kind="chat",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            duration_ms=result.duration_ms,
            tool_calls=[{"name": f"improve_text:{field}"}],
        )
        session.commit()
    except AIBackendError as exc:
        improved = body
        notice = f"AI error: {exc}"
        trace_service.record(
            session,
            model=cfg.model,
            task_id=task.row_id,
            kind="chat",
            error=str(exc),
        )
        session.commit()

    return _render_task_field_edit(
        request,
        project_name=proj.entry.name,
        task=task,
        field=field,
        raw_override=improved,
        intent=intent,
        notice=notice,
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
