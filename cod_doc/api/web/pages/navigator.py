"""Documentation Navigator: journey map + AI gap analysis routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config, get_project, get_project_db, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import nav_service

router = APIRouter()


@router.get("/p/{slug}/docs/navigator", response_class=HTMLResponse)
def doc_navigator(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    proj = get_project(slug)
    session, project_db_id = db
    journey = nav_service.compute_journey(session, project_db_id)
    total_docs = sum(step.count for step in journey)

    # Hot cache: if a fresh analysis exists, render it inline on the GET response
    # so the user does NOT pay an HTMX round-trip + loading spinner on repeat visits.
    cache_path = proj.entry.cod_doc_dir / "nav_cache.json"
    cached_analysis = nav_service.peek_cached_analysis(
        session, project_db_id, cache_path
    )

    return templates.TemplateResponse(
        request,
        "project/doc_navigator.html",
        {
            "project": {"name": proj.entry.name},
            "journey": journey,
            "total_docs": total_docs,
            "cached_analysis": cached_analysis,
        },
    )


@router.post("/p/{slug}/docs/navigator/analyze", response_class=HTMLResponse)
async def doc_navigator_analyze(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    force: bool = Form(False),
) -> HTMLResponse:
    """HTMX endpoint: run (or load cached) AI gap analysis, return fragment."""
    proj = get_project(slug)
    cfg = get_config()
    session, project_db_id = db
    cache_path = proj.entry.cod_doc_dir / "nav_cache.json"
    analysis = nav_service.analyze_gaps(
        session, project_db_id, cfg, cache_path, force=force
    )
    return templates.TemplateResponse(
        request,
        "_frag/nav_analysis.html",
        {
            "project": {"name": proj.entry.name},
            "analysis": analysis,
        },
    )
