"""Routines management page (PCA-919/920 follow-up UI)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import routine_service

router = APIRouter()


@router.get("/p/{slug}/routines", response_class=HTMLResponse)
def routines_list(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """List all routines + recent runs for a project."""
    proj = get_project(slug)
    session, project_db_id = db

    routines = routine_service.list_routines(session, project_db_id)
    rows: list[dict[str, Any]] = []
    for r in routines:
        try:
            history = routine_service.history(session, project_db_id, r.name, limit=1)
        except Exception:
            history = []
        last_run = history[0] if history else None
        rows.append({
            "name": r.name,
            "check_name": r.check_name,
            "trigger": r.trigger,
            "cron": r.cron or "",
            "on_finding": r.on_finding,
            "enabled": r.enabled,
            "last_run_at": last_run.started_at if last_run else None,
            "last_run_status": last_run.status if last_run else None,
            "last_findings": last_run.findings_count if last_run else None,
        })

    available_checks = sorted(routine_service.CHECK_CATALOG)

    return templates.TemplateResponse(
        request,
        "project/routines_list.html",
        {
            "project": {"name": proj.entry.name},
            "routines": rows,
            "available_checks": available_checks,
            "on_finding_options": ["comment_only", "create_task", "update_existing_task"],
        },
    )


@router.post("/p/{slug}/routines/create", response_class=HTMLResponse)
def routine_create(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    name: str = Form(...),
    check_name: str = Form(...),
    cron: str = Form("*/15 * * * *"),
    on_finding: str = Form("comment_only"),
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    try:
        routine_service.create(
            session,
            project_id=project_db_id,
            name=name.strip(),
            check_name=check_name.strip(),
            trigger="cron",
            cron=cron.strip() or None,
            on_finding=on_finding,
            enabled=True,
        )
        session.commit()
    except Exception as exc:
        raise HTTPException(400, f"Cannot create routine: {exc}") from exc
    return RedirectResponse(url=f"/p/{proj.entry.name}/routines", status_code=303)


@router.post("/p/{slug}/routines/{name}/toggle")
def routine_toggle(
    request: Request,
    slug: str,
    name: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> JSONResponse:
    """Flip enabled state."""
    session, project_db_id = db
    r = routine_service.get(session, project_db_id, name)
    if r is None:
        raise HTTPException(404, "Routine not found")
    routine_service.update_status(session, project_db_id, name, enabled=not r.enabled)
    session.commit()
    return JSONResponse({"name": name, "enabled": not r.enabled})


@router.post("/p/{slug}/routines/{name}/run")
def routine_run(
    request: Request,
    slug: str,
    name: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> JSONResponse:
    """Fire a routine immediately (manual trigger)."""
    session, project_db_id = db
    try:
        run = routine_service.run_now(session, project_db_id, name)
    except routine_service.RoutineNotFoundError:
        raise HTTPException(404, "Routine not found") from None
    session.commit()
    return JSONResponse({
        "name": name,
        "status": run.status,
        "findings_count": run.findings_count,
        "started_at": run.started_at.isoformat() if run.started_at else None,
    })


@router.post("/p/{slug}/routines/{name}/delete")
def routine_delete(
    request: Request,
    slug: str,
    name: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> Response:
    proj = get_project(slug)
    session, project_db_id = db
    try:
        routine_service.delete(session, project_db_id, name)
        session.commit()
    except routine_service.RoutineNotFoundError:
        raise HTTPException(404, "Routine not found") from None
    return RedirectResponse(url=f"/p/{proj.entry.name}/routines", status_code=303)
