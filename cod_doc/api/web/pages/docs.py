"""Document list / show / markdown import endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db, try_open_project_db
from cod_doc.api.web.errors import ValidationWebError
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import DocumentType, EntityKind
from cod_doc.services import doc_service as docs
from cod_doc.services import import_service as imports
from cod_doc.services import revision_service as revisions

router = APIRouter()


@router.get("/p/{slug}/docs", response_class=HTMLResponse)
def docs_list(request: Request, slug: str) -> HTMLResponse:
    proj = get_project(slug)
    documents: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for d in docs.list_for_project(session, project_db_id):
                documents.append(
                    {
                        "doc_key": d.doc_key,
                        "title": d.title,
                        "type": d.type.value,
                        "status": d.status.value,
                        "owner": d.owner or "",
                        "last_updated": d.last_updated,
                    }
                )
    return templates.TemplateResponse(
        request,
        "project/docs_list.html",
        {
            "project": {"name": proj.entry.name},
            "documents": documents,
            "db_available": db_available,
        },
    )


@router.post("/p/{slug}/docs/import", response_class=HTMLResponse)
def docs_import(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    doc_key: str = Form(""),
    type: str = Form("module-spec"),
    file: UploadFile = File(...),  # noqa: B008 — standard FastAPI form-upload pattern
) -> Response:
    """Upload a markdown file and create a Document + Sections.

    Frontmatter (if present) supplies title / type / status / owner /
    sensitivity; everything else falls back to defensible defaults.
    Sections are split on `## ` headings; preamble is everything before
    the first H2.
    """
    proj = get_project(slug)
    session, project_db_id = db

    if not doc_key.strip():
        raise ValidationWebError("doc_key обязателен")

    try:
        doc_type = DocumentType(type)
    except ValueError as exc:
        raise ValidationWebError(f"Неизвестный type: {type}") from exc

    raw_bytes = file.file.read()
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationWebError(
            "Файл не в UTF-8 — ожидается markdown в кодировке UTF-8."
        ) from exc

    fallback_title = (file.filename or doc_key).rsplit("/", 1)[-1]
    if fallback_title.endswith(".md"):
        fallback_title = fallback_title[:-3]

    try:
        doc = imports.import_markdown(
            session,
            project_id=project_db_id,
            doc_key=doc_key.strip(),
            raw_markdown=raw,
            fallback_title=fallback_title,
            fallback_type=doc_type,
            author="human:web",
            reason="web import",
        )
        session.commit()
    except (ValueError, IntegrityError) as exc:
        session.rollback()
        raise ValidationWebError(f"Импорт отклонён: {exc}") from exc

    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc.doc_key}", status_code=303
    )


@router.get("/p/{slug}/docs/{doc_key:path}", response_class=HTMLResponse)
def doc_show(
    request: Request,
    slug: str,
    doc_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    raw: int = 0,
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")
    sections_db = docs.get_sections(session, doc.row_id)

    # Sidebar nav uses the section anchors regardless of mode — they match
    # the `<section id>` we render below (or the in-page hash, harmless in
    # raw mode since browsers tolerate non-existent fragments).
    sections_nav = [
        {"anchor": s.anchor, "heading": s.heading, "level": s.level} for s in sections_db
    ]

    is_raw = bool(raw)
    raw_body: str | None = None
    preamble_html: str | None = None
    sections_html: list[dict[str, Any]] = []

    if is_raw:
        raw_body = docs.render_body(session, doc.row_id) or doc.preamble or ""
    else:
        preamble_html = render_markdown(doc.preamble or "") or None
        # WEB-012: expose head revision_id per section so the edit form can
        # send it back as `expected_parent_revision_id` for optimistic
        # concurrency. None when the section has no revisions yet (rare).
        sections_html = [
            {
                "anchor": s.anchor,
                "heading": s.heading,
                "level": s.level,
                "row_id": s.row_id,
                "body": s.body or "",
                "html": render_markdown(s.body or ""),
                "head_rev": revisions.head_for_entity(
                    session, EntityKind.SECTION, s.row_id
                )
                if s.row_id is not None
                else None,
            }
            for s in sections_db
        ]

    return templates.TemplateResponse(
        request,
        "project/doc_show.html",
        {
            "project": {"name": proj.entry.name},
            "doc": {
                "doc_key": doc.doc_key,
                "path": doc.path,
                "doc_id": doc.row_id,
                "title": doc.title,
                "type": doc.type.value,
                "status": doc.status.value,
                "owner": doc.owner or "",
                "last_updated": doc.last_updated,
            },
            "sections": sections_nav,
            "is_raw": is_raw,
            "raw_body": raw_body,
            "preamble_html": preamble_html,
            "sections_html": sections_html,
        },
    )
