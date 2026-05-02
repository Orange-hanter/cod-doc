"""Project-wide revisions log."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.deps import get_project, try_open_project_db
from cod_doc.api.web.templates_env import templates
from cod_doc.domain.entities import EntityKind
from cod_doc.services import revision_service as revisions

router = APIRouter()

REVISIONS_PAGE_LIMIT = 50


@router.get("/p/{slug}/revisions", response_class=HTMLResponse)
def revisions_log(
    request: Request,
    slug: str,
    entity_kind: str | None = None,
    entity_id: int | None = None,
) -> HTMLResponse:
    """Project-wide revisions log, optionally narrowed to one entity."""
    proj = get_project(slug)

    kind_filter: EntityKind | None = None
    kind_invalid = False
    if entity_kind:
        try:
            kind_filter = EntityKind(entity_kind)
        except ValueError:
            kind_invalid = True

    rows: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for r in revisions.list_for_project(
                session,
                project_db_id,
                limit=REVISIONS_PAGE_LIMIT,
                entity_kind=kind_filter,
                entity_id=entity_id if not kind_invalid else None,
            ):
                rows.append(
                    {
                        "revision_id": r.revision_id,
                        "entity_kind": r.entity_kind.value
                        if isinstance(r.entity_kind, EntityKind)
                        else str(r.entity_kind),
                        "entity_id": r.entity_id,
                        "author": r.author,
                        "at": r.at,
                        "reason": r.reason or "",
                        "diff_preview": (r.diff or "").splitlines()[0][:200] if r.diff else "",
                    }
                )

    return templates.TemplateResponse(
        request,
        "project/revisions.html",
        {
            "project": {"name": proj.entry.name},
            "revisions": rows,
            "db_available": db_available,
            "entity_kind": kind_filter.value if kind_filter else "",
            "entity_id": entity_id,
            "kind_invalid": kind_invalid,
            "kind_options": [k.value for k in EntityKind],
            "limit": REVISIONS_PAGE_LIMIT,
        },
    )
