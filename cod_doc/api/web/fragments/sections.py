"""Inline section view / edit / patch — WEB-012 with optimistic concurrency."""

from __future__ import annotations

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
from cod_doc.domain.entities import EntityKind
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as revisions
from cod_doc.services.doc_service import SectionNotFoundError
from cod_doc.services.revision_service import RevisionConflictError

from ._shared import _is_htmx

router = APIRouter()


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
