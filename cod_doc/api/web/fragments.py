"""HTMX fragment endpoints — return small HTML chunks for swap targets.

Convention: each fragment endpoint accepts both HTMX and non-HTMX form posts.
- HTMX request (`HX-Request: true`) → returns the fragment HTML.
- Regular form post → 303 redirect back to the parent list page (so the user
  sees the new state without an empty <tr> being rendered as a full page).

Exceptional errors (404 unknown task, 400 invalid form value) raise WebError
subclasses; the handler in `cod_doc.api.server` renders an alert fragment
(HTMX) or sets a cookie-flash + redirects (form). Recoverable errors that
also need a row update (conflict on update_status) keep returning the row
fragment AND append an OOB alert in the same response.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.errors import (
    ConflictWebError,
    NotFoundWebError,
    ValidationWebError,
    truncate_for_cookie,
)
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import EntityKind, TaskStatus
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as revisions
from cod_doc.services import task_service as tasks
from cod_doc.services.doc_service import SectionNotFoundError
from cod_doc.services.revision_service import RevisionConflictError
from cod_doc.services.task_service import TaskAlreadyDoneError, TaskBlockedError

router = APIRouter()


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _render_task_row(
    request: Request,
    *,
    project_name: str,
    task: Any,
    inline_alert: tuple[str, str] | None = None,
) -> HTMLResponse:
    """Render the task row, optionally followed by an OOB alert fragment.

    `inline_alert`, when set, is `(severity, message)` and produces an
    additional `<div class='alert' hx-swap-oob>` block appended to the row
    HTML. HTMX picks the OOB block separately and lands it in `#alerts`.
    """
    row_html = templates.get_template("_frag/task_row.html").render(
        project={"name": project_name},
        t={
            "task_id": task.task_id,
            "title": task.title,
            "type": task.type.value,
            "status": task.status.value,
            "priority": task.priority.value,
            "plan_id": task.plan_id,
            "section_id": task.section_id,
        },
    )
    if inline_alert is not None:
        severity, message = inline_alert
        alert_html = templates.get_template("_frag/alert.html").render(
            severity=severity, message=message, oob=True
        )
        return HTMLResponse(row_html + alert_html)
    return HTMLResponse(row_html)


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


# ── Section patch (WEB-012) ──────────────────────────────────────────────


def _render_section_view(
    request: Request, *, project_name: str, doc: Any, section: Any
) -> HTMLResponse:
    """Render the read-only section view fragment."""
    s_ctx = {
        "anchor": section.anchor,
        "heading": section.heading,
        "level": section.level,
        "html": render_markdown(section.body or ""),
    }
    html = templates.get_template("_frag/section_view.html").render(
        request=request,
        project={"name": project_name},
        doc={"doc_key": doc.doc_key},
        s=s_ctx,
    )
    return HTMLResponse(html)


def _render_section_edit(
    request: Request,
    *,
    project_name: str,
    doc: Any,
    section: Any,
    head_rev: str | None,
) -> HTMLResponse:
    s_ctx = {
        "anchor": section.anchor,
        "heading": section.heading,
        "level": section.level,
        "body": section.body or "",
        "head_rev": head_rev,
    }
    html = templates.get_template("_frag/section_edit.html").render(
        request=request,
        project={"name": project_name},
        doc={"doc_key": doc.doc_key},
        s=s_ctx,
    )
    return HTMLResponse(html)


def _resolve_section(
    session: Session, project_db_id: int, doc_key: str, anchor: str
) -> tuple[Any, Any]:
    """Find (doc, section) for the given (project, doc_key, anchor) or raise NotFound."""
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise NotFoundWebError(f"Документ не найден: {doc_key}")
    for s in docs.get_sections(session, doc.row_id):
        if s.anchor == anchor:
            return doc, s
    raise NotFoundWebError(f"Секция не найдена: {doc_key}#{anchor}")


@router.get(
    "/p/{slug}/docs/{doc_key:path}/sections/{anchor}/edit",
    response_class=HTMLResponse,
)
def section_edit_form(
    request: Request,
    slug: str,
    doc_key: str,
    anchor: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    doc, section = _resolve_section(session, project_db_id, doc_key, anchor)
    head_rev = revisions.head_for_entity(session, EntityKind.SECTION, section.row_id)
    return _render_section_edit(
        request,
        project_name=proj.entry.name,
        doc=doc,
        section=section,
        head_rev=head_rev,
    )


@router.get(
    "/p/{slug}/docs/{doc_key:path}/sections/{anchor}/view",
    response_class=HTMLResponse,
)
def section_view_fragment(
    request: Request,
    slug: str,
    doc_key: str,
    anchor: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Cancel button on the edit form swaps back to this view fragment."""
    proj = get_project(slug)
    session, project_db_id = db
    doc, section = _resolve_section(session, project_db_id, doc_key, anchor)
    return _render_section_view(
        request,
        project_name=proj.entry.name,
        doc=doc,
        section=section,
    )


@router.post(
    "/p/{slug}/docs/{doc_key:path}/sections/{anchor}",
    response_class=HTMLResponse,
)
def section_patch(
    request: Request,
    slug: str,
    doc_key: str,
    anchor: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    body: str = Form(...),
    expected_parent_revision_id: str = Form(""),
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    doc, _section = _resolve_section(session, project_db_id, doc_key, anchor)

    expected: str | None = expected_parent_revision_id or None
    try:
        updated = docs.patch_section(
            session,
            document_id=doc.row_id,
            anchor=anchor,
            new_body=body,
            author="human:web",
            reason="web inline section patch",
            expected_parent_revision_id=expected,
        )
        session.commit()
    except SectionNotFoundError as exc:
        session.rollback()
        raise NotFoundWebError(f"Секция исчезла: {doc_key}#{anchor}") from exc
    except RevisionConflictError as exc:
        session.rollback()
        # Re-read current section so the user sees the latest state inline.
        _doc2, current = _resolve_section(session, project_db_id, doc_key, anchor)
        raise ConflictWebError(
            f"Конфликт ревизий — секция была изменена другим автором. "
            f"Текущий head: {revisions.head_for_entity(session, EntityKind.SECTION, current.row_id)}"
        ) from exc
    except (IntegrityError, ValueError) as exc:
        session.rollback()
        raise ValidationWebError(str(exc)) from exc

    if _is_htmx(request):
        return _render_section_view(
            request,
            project_name=proj.entry.name,
            doc=doc,
            section=updated,
        )
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}#{anchor}", status_code=303
    )


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
