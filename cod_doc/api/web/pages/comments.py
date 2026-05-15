"""Document comments — section-anchored bubbles + doc-level review zone.

Comments are meta on the document: short notes the user pins to a
section (Google-Docs-style) or to the whole doc. The AI "apply" route
groups open comments by section and asks the model to rewrite each
section body so the comments are addressed; the result is rendered as
a preview the user can accept or discard.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config, get_project, get_project_db
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.services import comment_service as comments
from cod_doc.services import doc_service as docs

router = APIRouter()


@router.post(
    "/p/{slug}/docs/{doc_key:path}/comments",
    response_class=HTMLResponse,
)
async def comment_create(
    request: Request,
    slug: str,
    doc_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    form = await request.form()
    body = str(form.get("body") or "").strip()
    anchor = str(form.get("anchor") or "").strip() or None
    quote = str(form.get("quote") or "").strip() or None

    if not body:
        raise HTTPException(400, "Comment body is required")

    section_id: int | None = None
    if anchor:
        sections = docs.get_sections(session, doc.row_id)
        sec = next((s for s in sections if s.anchor == anchor), None)
        if sec is None or sec.row_id is None:
            raise HTTPException(404, f"Секция не найдена: {anchor}")
        section_id = sec.row_id

    try:
        comments.create(
            session,
            document_id=doc.row_id,
            section_id=section_id,
            anchor=anchor,
            quote=quote,
            body=body,
            author="human:web",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()

    # Keep the viewport where the user already is: section comments go
    # back to the section anchor; doc-level comments back to the comments
    # zone. Without this the user was always teleported to the bottom of
    # the doc after each comment submit.
    fragment = anchor if anchor else "comments"
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}#{fragment}", status_code=303
    )


@router.post(
    "/p/{slug}/docs/{doc_key:path}/comments/{comment_id}/resolve",
    response_class=HTMLResponse,
)
def comment_resolve(
    request: Request,
    slug: str,
    doc_key: str,
    comment_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    existing = comments.get(session, comment_id)
    if existing is None or existing.document_id != doc.row_id:
        raise HTTPException(404, "Comment not found in this document")

    fragment = existing.anchor or "comments"
    comments.update_status(session, comment_id=comment_id, new_status="resolved")
    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}#{fragment}", status_code=303
    )


@router.post(
    "/p/{slug}/docs/{doc_key:path}/comments/{comment_id}/reopen",
    response_class=HTMLResponse,
)
def comment_reopen(
    request: Request,
    slug: str,
    doc_key: str,
    comment_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    existing = comments.get(session, comment_id)
    if existing is None or existing.document_id != doc.row_id:
        raise HTTPException(404, "Comment not found in this document")

    fragment = existing.anchor or "comments"
    comments.update_status(session, comment_id=comment_id, new_status="open")
    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}#{fragment}", status_code=303
    )


@router.post(
    "/p/{slug}/docs/{doc_key:path}/comments/{comment_id}/delete",
    response_class=HTMLResponse,
)
def comment_delete(
    request: Request,
    slug: str,
    doc_key: str,
    comment_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    existing = comments.get(session, comment_id)
    if existing is None or existing.document_id != doc.row_id:
        raise HTTPException(404, "Comment not found in this document")

    fragment = existing.anchor or "comments"
    comments.delete(session, comment_id)
    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}#{fragment}", status_code=303
    )


@router.post(
    "/p/{slug}/docs/{doc_key:path}/comments/apply",
    response_class=HTMLResponse,
)
def comments_apply(
    request: Request,
    slug: str,
    doc_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Build an AI rework preview from all open comments and render it.

    No DB mutation happens here — the user reviews each section's
    proposed body and either accepts (a per-section apply form) or
    discards. ``commit_apply`` below is what actually writes.
    """
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    cfg = get_config()
    error: str | None = None
    rework = None
    try:
        rework = comments.apply_open_with_ai(
            session, document_id=doc.row_id, cfg=cfg
        )
    except Exception as exc:  # AIBackendError, ValueError, etc.
        error = str(exc)

    # Pre-render diffs as HTML so the template stays dumb.
    reworks_view = []
    if rework:
        for r in rework.section_reworks:
            reworks_view.append({
                "section_id": r.section_id,
                "anchor": r.anchor,
                "heading": r.heading,
                "comment_ids": r.comment_ids,
                "old_body": r.old_body,
                "new_body": r.new_body,
                "new_html": render_markdown(r.new_body),
            })

    return templates.TemplateResponse(
        request,
        "_frag/comment_apply_preview.html",
        {
            "project": {"name": proj.entry.name},
            "doc": {"doc_key": doc.doc_key, "doc_id": doc.row_id},
            "reworks": reworks_view,
            "doc_level_summary": rework.doc_level_summary if rework else "",
            "doc_level_comment_ids": rework.doc_level_comment_ids if rework else [],
            "skipped": rework.skipped if rework else [],
            "error": error,
        },
    )


@router.post(
    "/p/{slug}/docs/{doc_key:path}/comments/apply/commit",
    response_class=HTMLResponse,
)
async def comments_apply_commit(
    request: Request,
    slug: str,
    doc_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    """Persist accepted section reworks and flip comments to 'applied'.

    Expects form fields per accepted section:
    - ``apply_anchor``: list of anchors to apply
    - ``new_body__<anchor>``: the rewritten body the user wants to keep
    - ``comment_ids__<anchor>``: comma-separated comment IDs to mark applied
    - ``doc_level_comment_ids``: comma-separated doc-level IDs to mark applied
    """
    proj = get_project(slug)
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    form = await request.form()
    apply_anchors = [str(a) for a in form.getlist("apply_anchor")]

    applied_comment_ids: set[int] = set()
    sections_updated = 0

    for anchor in apply_anchors:
        body = str(form.get(f"new_body__{anchor}") or "")
        ids_raw = str(form.get(f"comment_ids__{anchor}") or "")
        try:
            docs.patch_section(
                session,
                document_id=doc.row_id,
                anchor=anchor,
                new_body=body,
                author="ai:apply-comments",
                reason=f"apply comments: {ids_raw}",
            )
            sections_updated += 1
        except docs.SectionNotFoundError:
            continue
        for cid in ids_raw.split(","):
            cid = cid.strip()
            if cid.isdigit():
                applied_comment_ids.add(int(cid))

    # Doc-level comments: user can opt to mark them applied without
    # patching anything (they're descriptive guidance).
    doc_level_raw = str(form.get("doc_level_comment_ids") or "")
    for cid in doc_level_raw.split(","):
        cid = cid.strip()
        if cid.isdigit():
            applied_comment_ids.add(int(cid))

    for cid in applied_comment_ids:
        try:
            comments.update_status(session, comment_id=cid, new_status="applied")
        except comments.CommentNotFoundError:
            pass

    session.commit()
    return RedirectResponse(
        url=f"/p/{proj.entry.name}/docs/{doc_key}#comments", status_code=303
    )


@router.get(
    "/p/{slug}/docs/{doc_key:path}/comments.json",
    response_class=JSONResponse,
)
def comments_json(
    request: Request,
    slug: str,
    doc_key: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> JSONResponse:
    """Read-only JSON dump — handy for the bubble click-popovers."""
    session, project_db_id = db
    doc = docs.get(session, project_db_id, doc_key)
    if doc is None or doc.row_id is None:
        raise HTTPException(404, f"Документ не найден: {doc_key}")

    rows = comments.list_for_document(session, doc.row_id)
    return JSONResponse([
        {
            "row_id": c.row_id,
            "section_id": c.section_id,
            "anchor": c.anchor,
            "quote": c.quote,
            "body": c.body,
            "author": c.author,
            "status": c.status,
            "created": c.created.isoformat(),
        }
        for c in rows
    ])
