"""Web pages: server-rendered HTML."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
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


def _masked_api_key(key: str) -> str:
    """Show only the last 4 chars; «sk-test1234» → «…1234»."""
    if not key:
        return ""
    if len(key) <= 4:
        return "…" * len(key)
    return "…" + key[-4:]


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

    # Prefer DB-aggregated plan stats; fall back to YAML stats if DB not ready.
    yaml_stats = Project.batch_stats(page_entries)
    projects = []
    for entry, fallback in zip(page_entries, yaml_stats, strict=True):
        stats = fallback
        with try_open_project_db(entry.name) as (session, project_db_id):
            if session is not None and project_db_id is not None:
                project_plans = plans.list_for_project(session, project_db_id)
                if project_plans:
                    total_tasks = done_tasks = in_progress_tasks = 0
                    for plan in project_plans:
                        assert plan.row_id is not None
                        p = plans.recalc(session, plan.row_id)
                        total_tasks += p.total
                        done_tasks += p.done
                        in_progress_tasks += p.in_progress
                    stats = {
                        **fallback,
                        "total": total_tasks,
                        "done": done_tasks,
                        "in_progress": in_progress_tasks,
                    }
        projects.append(
            {
                "name": entry.name,
                "path": entry.path,
                "enabled": entry.enabled,
                "stats": stats,
            }
        )

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
    master_html = render_markdown(master_preview or "") or None

    # WEB-014 — overview aggregator: ready-to-start tasks, plan-progress
    # mini-bars, recent revisions. Each block is independent and is left
    # empty (not crashed) if the DB project isn't initialised yet.
    ready_tasks: list[dict[str, Any]] = []
    plan_rows: list[dict[str, Any]] = []
    recent_revs: list[dict[str, Any]] = []
    db_available = False

    # Header KPI cards: prefer DB-aggregated totals (single source of truth
    # with the Plan-progress block below). Fall back to legacy YAML stats
    # when the DB isn't initialised — same shape so the template doesn't
    # need to branch.
    yaml_stats: dict[str, Any] = proj.stats()
    db_total = db_done = db_in_progress = 0

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
                db_total += progress.total
                db_done += progress.done
                db_in_progress += progress.in_progress
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

    if db_available and any((db_total, db_done, db_in_progress)):
        # Override pending/failed only when the DB has tasks; status/last_run
        # still come from the legacy state.yaml (the agent runtime writes there).
        kpi = {
            **yaml_stats,
            "total": db_total,
            "done": db_done,
            "in_progress": db_in_progress,
            "pending": db_total - db_done - db_in_progress,
        }
    else:
        kpi = yaml_stats

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
            "stats": kpi,
            "master_preview": master_preview,
            "master_html": master_html,
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
PLAN_READY_LIMIT = 7


@router.get("/p/{slug}/plans", response_class=HTMLResponse)
def plans_list(request: Request, slug: str) -> HTMLResponse:
    """List all plans of the project with current progress."""
    proj = get_project(slug)
    rows: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True
            for plan in plans.list_for_project(session, project_db_id):
                assert plan.row_id is not None
                progress = plans.recalc(session, plan.row_id)
                rows.append(
                    {
                        "plan_id": plan.row_id,
                        "scope": plan.scope,
                        "principle": plan.principle,
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
                        "last_updated": plan.last_updated,
                    }
                )
    return templates.TemplateResponse(
        request,
        "project/plans_list.html",
        {
            "project": {"name": proj.entry.name},
            "plans": rows,
            "db_available": db_available,
        },
    )


@router.get("/p/{slug}/plans/{plan_id}", response_class=HTMLResponse)
def plan_show(
    request: Request,
    slug: str,
    plan_id: int,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Plan detail: progress overview + ready batch + Mermaid graph."""
    proj = get_project(slug)
    session, project_db_id = db

    plan_dom = plans.get_for_project(session, project_db_id, plan_id)
    if plan_dom is None:
        raise HTTPException(404, f"Plan не найден в проекте: {plan_id}")

    progress = plans.recalc(session, plan_id)
    ready_tasks = plans.ready(session, plan_id, limit=PLAN_READY_LIMIT)
    exported = plans.export(session, plan_id)

    return templates.TemplateResponse(
        request,
        "project/plan_show.html",
        {
            "project": {"name": proj.entry.name},
            "plan": {
                "plan_id": plan_id,
                "scope": plan_dom.scope,
                "principle": plan_dom.principle,
                "status": progress.status.value,
                "total": progress.total,
                "done": progress.done,
                "in_progress": progress.in_progress,
                "remaining": progress.remaining,
                "percent": (
                    round(100 * progress.done / progress.total)
                    if progress.total
                    else 0
                ),
            },
            "sections": [
                {
                    "letter": s.letter,
                    "title": s.title,
                    "slug": s.slug,
                    "total": s.total,
                    "done": s.done,
                    "in_progress": s.in_progress,
                    "remaining": s.remaining,
                    "status": s.status.value,
                    "percent": (
                        round(100 * s.done / s.total) if s.total else 0
                    ),
                }
                for s in progress.sections
            ],
            "ready": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "type": t.type.value,
                    "status": t.status.value,
                    "priority": t.priority.value,
                    "section_id": t.section_id,
                }
                for t in ready_tasks
            ],
            "exported": exported,
        },
    )


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


@router.get("/p/{slug}/tasks/{task_id}", response_class=HTMLResponse)
def task_show(
    request: Request,
    slug: str,
    task_id: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> HTMLResponse:
    """Task detail: header + description + acceptance + chains + revisions."""
    proj = get_project(slug)
    session, project_db_id = db

    task = tasks.get(session, task_id)
    if task is None or task.project_id != project_db_id:
        raise HTTPException(404, f"Задача не найдена: {task_id}")
    assert task.row_id is not None

    forward = plans.forward_chain(session, task_id)
    reverse = plans.reverse_chain(session, task_id)
    history = revisions.list_for_entity(session, EntityKind.TASK, task.row_id)

    # Plan + section breadcrumb info.
    plan = plans.get_for_project(session, project_db_id, task.plan_id)

    return templates.TemplateResponse(
        request,
        "project/task_show.html",
        {
            "project": {"name": proj.entry.name},
            "task": {
                "task_id": task.task_id,
                "title": task.title,
                "type": task.type.value,
                "status": task.status.value,
                "priority": task.priority.value,
                "description": task.description or "",
                "description_html": render_markdown(task.description or ""),
                "acceptance": task.acceptance or "",
                "acceptance_html": render_markdown(task.acceptance or ""),
                "plan_id": task.plan_id,
                "section_id": task.section_id,
                "created": task.created,
                "last_updated": task.last_updated,
                "completed_at": task.completed_at,
                "completed_commit": task.completed_commit,
            },
            "plan": (
                {"plan_id": plan.row_id, "scope": plan.scope}
                if plan and plan.row_id is not None
                else None
            ),
            "forward": [
                {
                    "task_id": e.task_id,
                    "title": e.title,
                    "status": e.status.value,
                    "depth": e.depth,
                }
                for e in forward
            ],
            "reverse": [
                {
                    "task_id": e.task_id,
                    "title": e.title,
                    "status": e.status.value,
                    "depth": e.depth,
                }
                for e in reverse
            ],
            "history": [
                {
                    "revision_id": r.revision_id,
                    "author": r.author,
                    "at": r.at,
                    "reason": r.reason or "",
                    "diff_first_line": (r.diff or "").splitlines()[0][:240]
                    if r.diff
                    else "",
                }
                for r in reversed(history)  # newest first for the timeline
            ],
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
        # WEB-012: expose head revision_id per section so the edit form can
        # send it back as `expected_parent_revision_id` for optimistic
        # concurrency. None when the section has no revisions yet (rare).
        sections_html = [
            {
                "anchor": s.anchor,
                "heading": s.heading,
                "level": s.level,
                "row_id": s.row_id,
                "body": s.body or "",
                "html": render_markdown(s.body or ""),
                "head_rev": revisions.head_for_entity(
                    session, EntityKind.SECTION, s.row_id
                )
                if s.row_id is not None
                else None,
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
                "doc_id": doc.row_id,
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


# ── Settings (global config) — WEB-060 ────────────────────────────────────


@router.get("/settings", response_class=HTMLResponse)
def settings_show(request: Request) -> HTMLResponse:
    cfg = get_config()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "config": {
                "api_key_masked": _masked_api_key(cfg.api_key),
                "api_key_set": bool(cfg.api_key),
                "base_url": cfg.base_url,
                "model": cfg.model,
                "max_tokens": cfg.max_tokens,
                "auto_commit": cfg.auto_commit,
                "max_iterations": cfg.max_iterations,
                "agent_interval": cfg.agent_interval,
                "embedding_model": cfg.embedding_model,
            },
        },
    )


@router.post("/settings", response_class=HTMLResponse)
def settings_save(
    request: Request,
    api_key: str = Form(""),
    base_url: str = Form(...),
    model: str = Form(...),
    max_tokens: int = Form(...),
    auto_commit: str = Form(""),  # checkbox: "on" or absent
    max_iterations: int = Form(...),
    agent_interval: int = Form(...),
    embedding_model: str = Form(...),
) -> Response:
    cfg = get_config()
    # Empty api_key on POST means "leave existing untouched" — typical web UX
    # for password/secret fields. Forces an explicit "delete" by typing the
    # plain literal "-" (documented next to the field).
    if api_key == "-":
        cfg.api_key = ""
    elif api_key.strip():
        cfg.api_key = api_key.strip()
    cfg.base_url = base_url.strip()
    cfg.model = model.strip()
    cfg.max_tokens = max_tokens
    cfg.auto_commit = auto_commit == "on"
    cfg.max_iterations = max_iterations
    cfg.agent_interval = agent_interval
    cfg.embedding_model = embedding_model.strip()
    cfg.save()
    return RedirectResponse(url="/settings", status_code=303)
