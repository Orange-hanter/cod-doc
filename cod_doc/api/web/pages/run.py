"""Agent Console — live event stream + run history.

Renders ``/p/{slug}/run``. The page wires ``cod_doc_app.js`` to the
project WebSocket so the in-progress timeline refreshes in real time;
the right-rail "history" list comes from the ``agent_run`` table.

Run-detail drill-down lives at ``/p/{slug}/run/{run_id}`` and re-uses the
same template with a ``selected_run`` payload assembled from
``activity_event`` rows scoped to that run_id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import activity_service, run_service

router = APIRouter()

_RUN_HISTORY_LIMIT = 50


def _humanize_age(ts: datetime | None) -> str:
    """Render a datetime as a short relative age string ("2m", "3h", "5d")."""
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86_400:
        return f"{secs // 3600}h"
    return f"{secs // 86_400}d"


def _decorate_run(r: dict[str, Any]) -> dict[str, Any]:
    """Attach view-only fields (age) to a run dict from run_service."""
    return {**r, "age": _humanize_age(r["started_at"])}


@router.get("/p/{slug}/run", response_class=HTMLResponse)
def run_console(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Live agent console — current run + history sidebar."""
    proj = get_project(slug)
    session, project_db_id = db

    raw_runs = run_service.list_recent(session, project_db_id, limit=_RUN_HISTORY_LIMIT)
    runs = [_decorate_run(r) for r in raw_runs]
    active_run = next((r for r in runs if r["status"] == "running"), None)

    return templates.TemplateResponse(
        request,
        "project/run_console.html",
        {
            "project": {"name": proj.entry.name},
            "runs": runs,
            "active_run": active_run,
            "selected_run": None,
            "selected_events": [],
        },
    )


@router.get("/p/{slug}/run/{run_id}", response_class=HTMLResponse)
def run_detail(
    request: Request,
    slug: str,
    run_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Drill-down: events of a single past run, replayed onto the same console."""
    proj = get_project(slug)
    session, project_db_id = db

    run = run_service.get_one(session, project_db_id, run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    raw_runs = run_service.list_recent(session, project_db_id, limit=_RUN_HISTORY_LIMIT)
    runs = [_decorate_run(r) for r in raw_runs]
    active_run = next((r for r in runs if r["status"] == "running"), None)
    events = activity_service.events_for_run(session, project_db_id, run_id, limit=500)
    for e in events:
        # activity_service returns ts as ISO string; render a short HH:MM:SS
        # in the handler so the template stays string-only on event rows.
        if e.get("ts") and isinstance(e["ts"], str) and "T" in e["ts"]:
            e["ts_short"] = e["ts"].split("T", 1)[1][:8]
        else:
            e["ts_short"] = ""

    return templates.TemplateResponse(
        request,
        "project/run_console.html",
        {
            "project": {"name": proj.entry.name},
            "runs": runs,
            "active_run": active_run,
            "selected_run": _decorate_run(run),
            "selected_events": events,
        },
    )
