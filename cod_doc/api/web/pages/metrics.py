"""OBI-002: /p/<slug>/metrics — sparkline of completed tasks + percentiles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import metrics_service

router = APIRouter()


@router.get("/p/{slug}/metrics", response_class=HTMLResponse)
def metrics_dashboard(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    days: int = 30,
) -> HTMLResponse:
    """Per-project completion stats: sparkline + percentiles by task type."""
    proj = get_project(slug)
    session, project_id = db

    since = datetime.now(UTC) - timedelta(days=days)
    summary = metrics_service.summary(session, project_id, since=since)
    buckets = metrics_service.sparkline_buckets(session, project_id, days=days)

    # Sparkline rendering hints: max count per day (for bar heights),
    # average duration overlay (for tooltip), formatted percentile text.
    max_count = max((b["count"] for b in buckets), default=0)

    return templates.TemplateResponse(
        request,
        "project/metrics_dashboard.html",
        {
            "project": proj.entry,
            "days": days,
            "summary": summary,
            "buckets": buckets,
            "max_count": max_count,
            "has_data": summary["completed"] > 0,
        },
    )
