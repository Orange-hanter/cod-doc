"""ADR-004/005/006: web pages for Architecture Decision Records.

Routes:
- ``GET /p/{slug}/adr``                 — list with status filter (ADR-004).
- ``GET /p/{slug}/adr/new``             — create form (ADR-005).
- ``POST /p/{slug}/adr/new``            — submit; redirect to detail.
- ``GET /p/{slug}/adr/graph``           — supersede DAG (ADR-006).
- ``GET /p/{slug}/adr/{adr_id}``        — detail view (ADR-005).
- ``POST /p/{slug}/adr/{adr_id}/edit``  — patch fields; redirect to detail.
- ``POST /p/{slug}/adr/{adr_id}/diagram`` — attach a Mermaid diagram.
- ``POST /p/{slug}/adr/{adr_id}/supersede`` — record replaces edge.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.markdown import autolink_adr_refs, render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.services import adr_service
from cod_doc.services.adr_service import ADRAlreadyExistsError, ADRNotFoundError

router = APIRouter()


_STATUS_ICON = {
    "proposed": "✏️",
    "accepted": "✅",
    "superseded": "🔁",
    "deprecated": "⚠️",
    "rejected": "❌",
}


def _render_prose(text: str | None, slug: str) -> str:
    """Markdown-render an ADR body field and autolink bare ``ADR-NNN`` refs."""
    if not text:
        return ""
    return autolink_adr_refs(render_markdown(text), slug=slug)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return None


@router.get("/p/{slug}/adr", response_class=HTMLResponse)
def adr_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    status: str | None = None,
) -> HTMLResponse:
    """ADR-004: list page with status filter + badges."""
    proj = get_project(slug)
    session, project_id = db
    rows = adr_service.list_for_project(session, project_id, status=status)

    items = []
    for r in rows:
        items.append({
            "adr_id": r.adr_id,
            "title": r.title,
            "status": r.status,
            "status_icon": _STATUS_ICON.get(r.status, "•"),
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "author": r.author,
        })

    return templates.TemplateResponse(
        request,
        "project/adr_list.html",
        {
            "project": proj.entry,
            "items": items,
            "status_filter": status,
            "status_options": ["proposed", "accepted", "superseded", "deprecated", "rejected"],
        },
    )


@router.get("/p/{slug}/adr/new", response_class=HTMLResponse)
def adr_new_form(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """ADR-005: empty form to create a new ADR."""
    proj = get_project(slug)
    return templates.TemplateResponse(
        request,
        "project/adr_new.html",
        {
            "project": proj.entry,
            "form_action": f"/p/{slug}/adr/new",
            "status_options": ["proposed", "accepted", "superseded", "deprecated", "rejected"],
            "default_status": "proposed",
        },
    )


@router.post("/p/{slug}/adr/new")
def adr_new_submit(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    title: Annotated[str, Form()],
    status: Annotated[str, Form()] = "proposed",
    decided_at: Annotated[str | None, Form()] = None,
    context: Annotated[str | None, Form()] = None,
    decision: Annotated[str | None, Form()] = None,
    alternatives: Annotated[str | None, Form()] = None,
    consequences: Annotated[str | None, Form()] = None,
    adr_id: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """Create the ADR and redirect to its detail page."""
    session, project_id = db
    try:
        row = adr_service.create(
            session,
            project_id=project_id,
            title=title.strip(),
            status=status,
            decided_at=_parse_date(decided_at),
            context=(context or None),
            decision=(decision or None),
            alternatives=(alternatives or None),
            consequences=(consequences or None),
            adr_id=(adr_id.strip() if adr_id else None),
            author="human:web",
        )
        session.commit()
    except ADRAlreadyExistsError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{row.adr_id}", status_code=303)


@router.get("/p/{slug}/adr/graph", response_class=HTMLResponse)
def adr_graph_page(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """ADR-006: full supersede DAG rendered as a Mermaid block."""
    proj = get_project(slug)
    session, project_id = db
    graph = adr_service.graph(session, project_id)

    # Build mermaid source server-side so the template just renders.
    lines = ["graph LR"]
    for node in graph["nodes"]:
        node_id = node["adr_id"].replace("-", "_")
        icon = _STATUS_ICON.get(node["status"], "•")
        # Mermaid label: id + status icon + title; keep it short.
        title = node["title"].replace('"', "'").replace("\n", " ")
        if len(title) > 40:
            title = title[:37] + "…"
        label = f"{icon} {node['adr_id']}<br/>{title}"
        lines.append(f"  {node_id}[\"{label}\"]")
    for edge in graph["edges"]:
        from_id = edge["from"].replace("-", "_")
        to_id = edge["to"].replace("-", "_")
        lines.append(f"  {from_id} --> {to_id}")
    mermaid_src = "\n".join(lines)

    return templates.TemplateResponse(
        request,
        "project/adr_graph.html",
        {
            "project": proj.entry,
            "graph": graph,
            "mermaid": mermaid_src,
            "has_nodes": bool(graph["nodes"]),
        },
    )


@router.get("/p/{slug}/adr/{adr_id}", response_class=HTMLResponse)
def adr_show(
    request: Request,
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """ADR-005: detail page with diagrams + task links + edit form."""
    proj = get_project(slug)
    session, project_id = db
    row = adr_service.get(session, project_id, adr_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"ADR {adr_id} not found")
    payload = adr_service.adr_to_dict(session, row)
    payload["status_icon"] = _STATUS_ICON.get(payload["status"], "•")
    payload["context_html"] = _render_prose(payload.get("context"), slug)
    payload["decision_html"] = _render_prose(payload.get("decision"), slug)
    payload["alternatives_html"] = _render_prose(payload.get("alternatives"), slug)
    payload["consequences_html"] = _render_prose(payload.get("consequences"), slug)

    # Candidate ADRs for the supersede dropdown (everything except self).
    candidates = [
        {"adr_id": r.adr_id, "title": r.title, "status": r.status}
        for r in adr_service.list_for_project(session, project_id)
        if r.adr_id != adr_id
    ]

    return templates.TemplateResponse(
        request,
        "project/adr_show.html",
        {
            "project": proj.entry,
            "adr": payload,
            "candidates": candidates,
            "status_options": ["proposed", "accepted", "superseded", "deprecated", "rejected"],
        },
    )


@router.post("/p/{slug}/adr/{adr_id}/edit")
def adr_edit(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    title: Annotated[str, Form()],
    status: Annotated[str, Form()],
    decided_at: Annotated[str | None, Form()] = None,
    context: Annotated[str | None, Form()] = None,
    decision: Annotated[str | None, Form()] = None,
    alternatives: Annotated[str | None, Form()] = None,
    consequences: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    session, project_id = db
    try:
        adr_service.update(
            session, project_id=project_id, adr_id=adr_id,
            title=title.strip(), status=status,
            decided_at=_parse_date(decided_at),
            context=(context or None),
            decision=(decision or None),
            alternatives=(alternatives or None),
            consequences=(consequences or None),
            author="human:web",
        )
        session.commit()
    except ADRNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)


@router.post("/p/{slug}/adr/{adr_id}/diagram")
def adr_add_diagram(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    mermaid: Annotated[str, Form()],
    title: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    session, project_id = db
    try:
        adr_service.add_diagram(
            session, project_id=project_id, adr_id=adr_id,
            mermaid=mermaid, title=(title or None),
            author="human:web",
        )
        session.commit()
    except ADRNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)


@router.post("/p/{slug}/adr/{adr_id}/supersede")
def adr_supersede_post(
    slug: str,
    adr_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    superseded_adr_id: Annotated[str, Form()],
    reason: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """``adr_id`` is the *new* ADR that supersedes ``superseded_adr_id``."""
    session, project_id = db
    try:
        adr_service.supersede(
            session, project_id=project_id,
            superseding_adr_id=adr_id, superseded_adr_id=superseded_adr_id,
            reason=(reason or None),
            author="human:web",
        )
        session.commit()
    except ADRNotFoundError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/adr/{adr_id}", status_code=303)
