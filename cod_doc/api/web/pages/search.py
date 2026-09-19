"""OBI-040: /p/<slug>/search — unified FTS5 search across tasks/docs/stories/ADRs."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project, get_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.services import search_service

router = APIRouter()


_DEEPLINK_BY_KIND = {
    "task": lambda slug, ref: f"/p/{slug}/tasks/{ref}",
    "doc": lambda slug, ref: f"/p/{slug}/docs/{ref}",
    "story": lambda slug, ref: f"/p/{slug}/stories/{ref}",
    "adr": lambda slug, ref: f"/p/{slug}/adr/{ref}",
}


@router.get("/p/{slug}/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    q: str = "",
    scope: str | None = None,
    limit: int = 20,
    index_error: str | None = None,
) -> HTMLResponse:
    """Search form + result groups.

    CUR-010: a project DB that predates migration 0023 has no
    ``db_search_idx`` table — ``search_service.search`` raises
    ``SearchIndexMissing`` instead of the raw ``OperationalError`` that used
    to bubble up as an unhandled 500. Caught here and rendered as a plain
    message on the page (``index_error`` also arrives via the query string
    when ``search_reindex`` below hits the same guard and redirects back).
    """
    proj = get_project(slug)
    session, project_id = db

    result: dict[str, Any] | None = None
    error_message = index_error
    if q.strip():
        try:
            result = search_service.search(
                session,
                project_id=project_id,
                query=q,
                scope=(scope or None),
                limit=limit,
            )
        except search_service.SearchIndexMissing as exc:
            error_message = str(exc)
        else:
            # Decorate each hit with a deep link.
            for kind, hits in result["by_kind"].items():
                mk = _DEEPLINK_BY_KIND.get(kind)
                for h in hits:
                    h["url"] = mk(slug, h["ref"]) if mk else None

    return templates.TemplateResponse(
        request,
        "project/search.html",
        {
            "project": proj.entry,
            "q": q,
            "scope": scope,
            "result": result,
            "index_error": error_message,
            "scopes": ["task", "doc", "story", "adr", "finding"],
        },
    )


@router.post("/p/{slug}/search/reindex")
def search_reindex(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> RedirectResponse:
    """Rebuild FTS index for this project."""
    session, project_id = db
    try:
        counts = search_service.reindex_all(session, project_id)
    except search_service.SearchIndexMissing as exc:
        session.rollback()
        return RedirectResponse(
            url=f"/p/{slug}/search?q=&index_error={quote(str(exc))}",
            status_code=303,
        )
    session.commit()
    # Pass counts as a flash via query string — kept simple for now.
    total = counts.get("total", 0)
    return RedirectResponse(
        url=f"/p/{slug}/search?q=&reindexed={total}",
        status_code=303,
    )
