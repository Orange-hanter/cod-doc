"""Web pages: server-rendered HTML."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import (
    get_config,
    get_project,
    get_project_db,
    try_open_project_db,
)
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import Project
from cod_doc.domain.entities import EntityKind, TaskStatus
from cod_doc.services import doc_service as docs
from cod_doc.services import plan_service as plans
from cod_doc.services import revision_service as revisions
from cod_doc.services import task_service as tasks

router = APIRouter()

MASTER_PREVIEW_LINES = 80
INDEX_DEFAULT_LIMIT = 20
INDEX_MAX_LIMIT = 200


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    limit: int = INDEX_DEFAULT_LIMIT,
    offset: int = 0,
) -> HTMLResponse:
    cfg = get_config()
    all_entries = cfg.list_projects()
    total = len(all_entries)
    # Clamp to defensive bounds — page sizes are user-supplied query params.
    limit = max(1, min(limit, INDEX_MAX_LIMIT))
    offset = max(0, offset)
    page_entries = all_entries[offset : offset + limit]

    # Parallelise the per-project stats() reads to avoid N×sequential I/O on
    # the index page (WEB-013, audit SW-HI-3).
    page_stats = Project.batch_stats(page_entries)
    projects = [
        {
            "name": entry.name,
            "path": entry.path,
            "enabled": entry.enabled,
            "stats": stats,
        }
        for entry, stats in zip(page_entries, page_stats, strict=True)
    ]

    has_prev = offset > 0
    has_next = offset + limit < total
    prev_offset = max(0, offset - limit)
    next_offset = offset + limit

    # Empty page (offset >= total OR no projects at all) → show "0–0 of N"
    # rather than a backwards range like "11–10 of 10".
    if projects:
        showing_from = offset + 1
        showing_to = offset + len(projects)
    else:
        showing_from = 0
        showing_to = 0

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "projects": projects,
            "configured": cfg.is_configured,
            "page": {
                "limit": limit,
                "offset": offset,
                "total": total,
                "has_prev": has_prev,
                "has_next": has_next,
                "prev_offset": prev_offset,
                "next_offset": next_offset,
                "showing_from": showing_from,
                "showing_to": showing_to,
            },
        },
    )


OVERVIEW_READY_LIMIT = 5
OVERVIEW_REVISIONS_LIMIT = 5


@router.get("/p/{slug}", response_class=HTMLResponse)
def project_show(request: Request, slug: str) -> HTMLResponse:
    proj = get_project(slug)
    master = proj.read_master()
    master_preview, master_truncated = _preview(master, MASTER_PREVIEW_LINES)

    # WEB-014 — overview aggregator: ready-to-start tasks, plan-progress
    # mini-bars, recent revisions. Each block is independent and is left
    # empty (not crashed) if the DB project isn't initialised yet.
    ready_tasks: list[dict[str, Any]] = []
    plan_rows: list[dict[str, Any]] = []
    recent_revs: list[dict[str, Any]] = []
    db_available = False

    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            project_plans = plans.list_for_project(session, project_db_id)
            for plan in project_plans:
                # `row_id` is Optional in the domain dataclass (used for
                # not-yet-persisted entities); list_for_project always
                # returns persisted rows.
                assert plan.row_id is not None
                progress = plans.recalc(session, plan.row_id)
                plan_rows.append(
                    {
                        "plan_id": plan.row_id,
                        "scope": plan.scope,
                        "total": progress.total,
                        "done": progress.done,
                        "in_progress": progress.in_progress,
                        "remaining": progress.remaining,
                        "status": progress.status.value,
                        "percent": (
                            round(100 * progress.done / progress.total)
                            if progress.total
                            else 0
                        ),
                    }
                )
                # Top-N ready-to-start across all plans, capped overall.
                if len(ready_tasks) < OVERVIEW_READY_LIMIT:
                    for t in plans.ready(
                        session, plan.row_id, limit=OVERVIEW_READY_LIMIT - len(ready_tasks)
                    ):
                        ready_tasks.append(
                            {
                                "task_id": t.task_id,
                                "title": t.title,
                                "type": t.type.value,
                                "status": t.status.value,
                                "priority": t.priority.value,
                                "plan_id": t.plan_id,
                                "section_id": t.section_id,
                            }
                        )

            for r in revisions.list_recent_for_project(
                session, project_db_id, limit=OVERVIEW_REVISIONS_LIMIT
            ):
                recent_revs.append(
                    {
                        "revision_id": r.revision_id,
                        "entity_kind": r.entity_kind.value
                        if isinstance(r.entity_kind, EntityKind)
                        else str(r.entity_kind),
                        "entity_id": r.entity_id,
                        "author": r.author,
                        "at": r.at,
                        "reason": r.reason or "",
                    }
                )

    return templates.TemplateResponse(
        request,
        "project/show.html",
        {
            "project": {
                "name": proj.entry.name,
                "path": proj.entry.path,
                "enabled": proj.entry.enabled,
                "master_md": proj.entry.master_md,
                "master_exists": proj.entry.master_path.exists(),
            },
            "stats": proj.stats(),
            "master_preview": master_preview,
            "master_truncated": master_truncated,
            "db_available": db_available,
            "ready_tasks": ready_tasks,
            "plan_rows": plan_rows,
            "recent_revs": recent_revs,
        },
    )


def _preview(text: str | None, max_lines: int) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[:max_lines]), True


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


@router.get("/p/{slug}/tasks", response_class=HTMLResponse)
def tasks_list(
    request: Request,
    slug: str,
    status: str | None = None,
) -> HTMLResponse:
    proj = get_project(slug)

    status_filter: TaskStatus | None = None
    status_invalid = False
    if status:
        try:
            status_filter = TaskStatus(status)
        except ValueError:
            status_invalid = True

    rows: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for t in tasks.list_for_project(session, project_db_id, status=status_filter):
                rows.append(
                    {
                        "task_id": t.task_id,
                        "title": t.title,
                        "status": t.status.value,
                        "type": t.type.value,
                        "priority": t.priority.value,
                        "plan_id": t.plan_id,
                        "section_id": t.section_id,
                    }
                )
    return templates.TemplateResponse(
        request,
        "project/tasks_list.html",
        {
            "project": {"name": proj.entry.name},
            "tasks": rows,
            "db_available": db_available,
            "status_filter": status_filter.value if status_filter else "",
            "status_invalid": status_invalid,
        },
    )


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
        sections_html = [
            {
                "anchor": s.anchor,
                "heading": s.heading,
                "level": s.level,
                "html": render_markdown(s.body or ""),
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
