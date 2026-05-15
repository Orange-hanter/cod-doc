"""OBI-011: /p/<slug>/commits — task↔commit links index + manual import trigger."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import commit_link_service

router = APIRouter()


@router.get("/p/{slug}/commits", response_class=HTMLResponse)
def commits_index(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    limit: int = 100,
) -> HTMLResponse:
    """Newest-first table of commit↔task links."""
    proj = get_project(slug)
    session, project_id = db
    rows = commit_link_service.list_for_project(session, project_id, limit=limit)
    items = [
        {
            "task_id": r.task_id,
            "sha": r.sha,
            "short_sha": r.short_sha,
            "message": r.message,
            "author": r.author,
            "ts": r.ts.isoformat() if r.ts else None,
        }
        for r in rows
    ]
    return templates.TemplateResponse(
        request,
        "project/commits_list.html",
        {
            "project": proj.entry,
            "items": items,
            "limit": limit,
            "has_data": bool(items),
        },
    )


@router.post("/p/{slug}/commits/import")
def commits_import(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> RedirectResponse:
    """Re-scan ``git log`` of the project root and persist new task-tagged commits.

    Idempotent — already-known (project, task, sha) tuples are skipped.
    """
    proj = get_project(slug)
    session, project_id = db
    try:
        commit_link_service.import_from_git_log(
            session, project_id=project_id, repo_path=Path(proj.entry.path),
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return RedirectResponse(url=f"/p/{slug}/commits", status_code=303)
