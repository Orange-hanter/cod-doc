"""Task list + detail handlers."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_config, get_project, get_project_db, try_open_project_db
from cod_doc.api.web.markdown import render_markdown
from cod_doc.api.web.templates_env import templates
from cod_doc.core.project import TaskStatus as LegacyTaskStatus
from cod_doc.domain.entities import EntityKind, TaskStatus
from cod_doc.services import plan_service as plans
from cod_doc.services import revision_service as revisions
from cod_doc.services import task_service as tasks
from cod_doc.services import trace_service as traces

router = APIRouter()

LEGACY_TASK_STATUS_OPTIONS = [s.value for s in LegacyTaskStatus]
LEGACY_PAGE_SIZE_DEFAULT = 100
LEGACY_PAGE_SIZE_MAX = 500

# Kanban columns in workflow order.  Each column collects multiple status
# aliases (e.g. legacy `pending` + new `todo`) under one bucket.
# Tuple: (column_key, display_label, icon, set_of_status_values).
_KANBAN_COLS: list[tuple[str, str, str, set[str]]] = [
    ("todo",        "Todo",        "○", {"backlog", "todo", "pending"}),
    ("in_progress", "In progress", "◐", {"in_progress", "in-progress"}),
    ("in_review",   "In review",   "◔", {"in_review"}),
    ("blocked",     "Blocked",     "✕", {"blocked"}),
    ("done",        "Done",        "●", {"done"}),
    ("cancelled",   "Cancelled",   "—", {"cancelled"}),
]

# Type-letter glyphs for compact task cards.
_TYPE_GLYPHS: dict[str, str] = {
    "feature":  "F",
    "bug":      "B",
    "refactor": "R",
    "test":     "T",
    "docs":     "D",
    "chore":    "C",
}

# Priority sort weight (critical first).
_PRIO_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _column_for(status: str) -> str | None:
    """Map a task status value to its kanban column key."""
    for key, _label, _icon, members in _KANBAN_COLS:
        if status in members:
            return key
    return None


def _enrich_chain(
    session: Session,
    plan_id: int,
    scope: str,
) -> dict[str, Any]:
    """Wrap ``plan_service.chain_layout`` with display-only fields (scope label,
    type glyphs, priority sort) so the template stays free of computation.
    """
    raw = plans.chain_layout(session, plan_id)
    levels: list[dict[str, Any]] = []
    for lvl_bucket in raw["levels"]:
        tasks_with_glyph = [
            {**t, "type_glyph": _TYPE_GLYPHS.get(t["type"], "?")}
            for t in lvl_bucket["tasks"]
        ]
        tasks_with_glyph.sort(key=lambda c: (_PRIO_RANK.get(c["priority"], 99), c["task_id"]))
        levels.append({"level": lvl_bucket["level"], "tasks": tasks_with_glyph})

    return {
        "scope": scope,
        "levels": levels,
        "critical_path": raw["critical_path"],
        "ready_ids": raw["ready_ids"],
        "edge_count": raw["edge_count"],
        "task_count": raw["task_count"],
        "max_level": raw["max_level"],
    }


@router.get("/p/{slug}/tasks", response_class=HTMLResponse)
def tasks_list(
    request: Request,
    slug: str,
    plan: str | None = None,
    status: str | None = None,
    view: str = "board",
) -> HTMLResponse:
    """Task list with two layout modes:

    * ``?view=board`` (default) — kanban board grouped by status.
    * ``?view=chains`` — dependency-graph view: tasks arranged in topological
      levels per plan, critical path highlighted, ready set marked.

    ``?plan=<scope>`` restricts the rendering to one plan. ``?status=<value>``
    in board mode highlights the matching column.
    """
    proj = get_project(slug)
    if view not in ("board", "chains"):
        view = "board"

    status_filter: TaskStatus | None = None
    status_invalid = False
    if status:
        try:
            status_filter = TaskStatus(status)
        except ValueError:
            status_invalid = True

    all_rows: list[dict[str, Any]] = []
    plan_list: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    db_available = False
    with try_open_project_db(slug) as (session, project_db_id):
        if session is not None and project_db_id is not None:
            db_available = True

            plans_models = plans.list_for_project(session, project_db_id)
            progress_map = plans.recalc_for_project(session, project_db_id)
            scope_by_pid: dict[int, str] = {}
            sections_by_pid: dict[int, dict[int, dict[str, str]]] = {}
            for p in plans_models:
                if p.row_id is None:
                    continue
                scope_by_pid[p.row_id] = p.scope
                sects = plans.list_sections(session, p.row_id)
                sections_by_pid[p.row_id] = {
                    s.row_id: {"letter": s.letter or "", "title": s.title}
                    for s in sects if s.row_id is not None
                }
                prog = progress_map.get(p.row_id)
                plan_list.append({
                    "scope": p.scope,
                    "done": prog.done if prog else 0,
                    "total": prog.total if prog else 0,
                })

            for t in tasks.list_for_project(session, project_db_id):
                plan_scope = scope_by_pid.get(t.plan_id, "")
                sect = sections_by_pid.get(t.plan_id, {}).get(t.section_id, {})
                has_ac = bool(t.acceptance and t.acceptance.strip())
                has_desc = bool(t.description and t.description.strip())
                all_rows.append({
                    "task_id": t.task_id,
                    "title": t.title,
                    "status": t.status.value,
                    "type": t.type.value,
                    "type_glyph": _TYPE_GLYPHS.get(t.type.value, "?"),
                    "priority": t.priority.value,
                    "plan_id": t.plan_id,
                    "plan_scope": plan_scope,
                    "section_letter": sect.get("letter", ""),
                    "section_title": sect.get("title", ""),
                    "has_acceptance": has_ac,
                    "has_description": has_desc,
                    "blocked_reason": t.blocked_reason or "",
                })

            # Compute per-plan chain data only when the chains view is requested
            # (it issues one extra recursive CTE per plan for critical_path).
            if view == "chains":
                for p in plans_models:
                    if p.row_id is None:
                        continue
                    if plan and p.scope != plan:
                        continue
                    chain = _enrich_chain(session, p.row_id, p.scope)
                    if chain["task_count"] > 0:
                        chains.append(chain)

    # Apply plan filter (if any).  Plan filter is by scope string.
    rows = all_rows
    if plan:
        rows = [r for r in rows if r["plan_scope"] == plan]

    # Stats are computed on the (plan-filtered) row set so they match what's
    # visible on the board.
    total = len(rows)
    stats = {
        "total": total,
        "done": sum(1 for r in rows if r["status"] == "done"),
        "in_progress": sum(1 for r in rows if r["status"] in ("in_progress", "in-progress")),
        "blocked": sum(1 for r in rows if r["status"] == "blocked"),
        "in_review": sum(1 for r in rows if r["status"] == "in_review"),
        "missing_ac": sum(1 for r in rows if not r["has_acceptance"] and r["status"] != "done"),
        "critical_open": sum(1 for r in rows
                             if r["priority"] == "critical" and r["status"] != "done"),
    }
    stats["pct_done"] = int(stats["done"] / total * 100) if total else 0

    # Bucket rows into kanban columns + priority-sort within each column.
    columns: list[dict[str, Any]] = []
    for key, label, icon, _members in _KANBAN_COLS:
        col_tasks = [r for r in rows if _column_for(r["status"]) == key]
        col_tasks.sort(key=lambda r: (_PRIO_RANK.get(r["priority"], 99), r["task_id"]))
        columns.append({
            "key": key,
            "label": label,
            "icon": icon,
            "count": len(col_tasks),
            "tasks": col_tasks,
            # Collapsed by default if Done/Cancelled — least scanned columns.
            "collapsed_default": key in ("done", "cancelled"),
            "highlighted": status_filter is not None and status_filter.value in _members,
        })

    legacy_count = len(proj.get_tasks())
    return templates.TemplateResponse(
        request,
        "project/tasks_list.html",
        {
            "project": {"name": proj.entry.name},
            "tasks": rows,
            "columns": columns,
            "chains": chains,
            "view": view,
            "stats": stats,
            "plans": sorted(plan_list, key=lambda p: p["scope"]),
            "selected_plan": plan or "",
            "db_available": db_available,
            "status_filter": status_filter.value if status_filter else "",
            "status_invalid": status_invalid,
            "legacy_count": legacy_count,
        },
    )


@router.post("/p/{slug}/tasks/audit", response_class=HTMLResponse)
def tasks_audit(
    request: Request,
    slug: str,
) -> HTMLResponse:
    """Run AI consistency audit over all project tasks.

    Checks: missing descriptions/acceptance, stuck blocked tasks, sequencing
    gaps, orphaned tasks, priority inconsistencies. Caches to task_audit.json.
    """
    from cod_doc.services.ai_text import AIBackendError, _call_lite_raw

    proj = get_project(slug)
    cfg = get_config()

    all_tasks_data: list[dict[str, Any]] = []
    with try_open_project_db(slug) as (session, project_db_id):
        if session is None or project_db_id is None:
            return templates.TemplateResponse(
                request,
                "_frag/task_audit.html",
                {
                    "project": {"name": slug},
                    "audit": {"error": "Database not available for this project."},
                },
            )
        for t in tasks.list_for_project(session, project_db_id):
            all_tasks_data.append(
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "status": t.status.value,
                    "type": t.type.value,
                    "priority": t.priority.value,
                    "plan_id": t.plan_id,
                    "section_id": t.section_id,
                    "has_description": bool(t.description and t.description.strip()),
                    "has_acceptance": bool(t.acceptance and t.acceptance.strip()),
                    "blocked_reason": t.blocked_reason or "",
                }
            )

    tasks_json = json.dumps(all_tasks_data, ensure_ascii=False)
    prompt = (
        "You are a senior project manager auditing a task list for an engineering team.\n\n"
        f"Task list (JSON):\n{tasks_json}\n\n"
        "Analyze all tasks and return a JSON object with these exact fields:\n"
        '{\n'
        '  "summary": "2-3 sentence overall assessment",\n'
        '  "score": 7,\n'
        '  "issues": [\n'
        '    {"severity": "critical|warning|info", "category": "short label",\n'
        '     "message": "concrete description", "task_ids": ["T-001", ...]}\n'
        '  ],\n'
        '  "recommendations": ["actionable step", ...],\n'
        '  "strengths": ["positive observation", ...]\n'
        '}\n\n'
        "Check for: missing descriptions or acceptance criteria; tasks stuck as "
        "'blocked' with no blocked_reason; 'in_progress' tasks that look stale; "
        "sequencing issues (high-priority tasks that may depend on lower-priority "
        "ones); plan/section distribution imbalances; duplicate or vague titles; "
        "any obvious coverage gaps.\n"
        "Max 8 issues, 5 recommendations, 4 strengths. "
        "Respond ONLY with valid JSON, no markdown fences."
    )

    audit_path = proj.entry.cod_doc_dir / "task_audit.json"
    try:
        raw = _call_lite_raw(prompt, cfg, max_tokens=2048).strip()
        if raw.startswith("```"):
            raw_lines = raw.splitlines()
            raw = "\n".join(
                raw_lines[1:-1] if raw_lines[-1].strip() == "```" else raw_lines[1:]
            )
        data = json.loads(raw)
        audit = {
            "summary": str(data.get("summary", "")),
            "score": int(data.get("score", 0)),
            "issues": [
                {
                    "severity": str(i.get("severity", "info")),
                    "category": str(i.get("category", "")),
                    "message": str(i.get("message", "")),
                    "task_ids": [str(x) for x in i.get("task_ids", [])],
                }
                for i in data.get("issues", [])
            ][:8],
            "recommendations": [str(x) for x in data.get("recommendations", [])][:5],
            "strengths": [str(x) for x in data.get("strengths", [])][:4],
            "generated_at": __import__("datetime").datetime.now(
                __import__("datetime").UTC
            ).isoformat(),
            "task_count": len(all_tasks_data),
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    except (AIBackendError, json.JSONDecodeError, Exception) as exc:
        audit = {
            "summary": "",
            "score": 0,
            "issues": [],
            "recommendations": [],
            "strengths": [],
            "generated_at": "",
            "task_count": len(all_tasks_data),
            "error": str(exc),
        }

    return templates.TemplateResponse(
        request,
        "_frag/task_audit.html",
        {"project": {"name": proj.entry.name}, "audit": audit},
    )


@router.get("/p/{slug}/tasks/legacy", response_class=HTMLResponse)
def tasks_legacy_list(
    request: Request,
    slug: str,
    status: str | None = None,
    limit: int = LEGACY_PAGE_SIZE_DEFAULT,
    offset: int = 0,
) -> HTMLResponse:
    """Render legacy YAML-stored tasks (.cod-doc/tasks.yaml) — paginated.

    These predate the DB schema and remain visible in the UI so importers
    and audits can see what is still un-migrated.
    """
    proj = get_project(slug)

    status_filter: LegacyTaskStatus | None = None
    status_invalid = False
    if status:
        try:
            status_filter = LegacyTaskStatus(status)
        except ValueError:
            status_invalid = True

    limit = max(1, min(limit, LEGACY_PAGE_SIZE_MAX))
    offset = max(0, offset)

    all_tasks = proj.get_tasks(status_filter)
    total = len(all_tasks)
    page = all_tasks[offset : offset + limit]

    rows: list[dict[str, Any]] = []
    for t in page:
        rows.append(
            {
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority,
                "updated": t.updated,
            }
        )

    return templates.TemplateResponse(
        request,
        "project/tasks_legacy_list.html",
        {
            "project": {"name": proj.entry.name},
            "tasks": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
            "status_filter": status_filter.value if status_filter else "",
            "status_invalid": status_invalid,
            "legacy_status_options": LEGACY_TASK_STATUS_OPTIONS,
        },
    )


@router.post("/p/{slug}/tasks/legacy/import")
def legacy_tasks_import(
    request: Request,
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    dry_run: bool = Query(default=False),
) -> JSONResponse:
    """PCA-410: Migrate (or preview) legacy YAML tasks → DB.

    With ``?dry_run=true`` the session is rolled back and a diff is returned
    without writing anything.  Without it, tasks are committed.

    Returns JSON:
    ``{"imported": N, "skipped": N, "errors": [...], "plan_scope": "...", "dry_run": bool}``
    """
    from cod_doc.services import restate_importer

    proj = get_project(slug)
    session, project_db_id = db

    yaml_path = proj.entry.cod_doc_dir / "tasks.yaml"
    if not yaml_path.exists():
        archived = proj.entry.cod_doc_dir / "tasks.archived.yaml"
        if archived.exists():
            raise HTTPException(409, "tasks.yaml already archived — legacy tasks already migrated")
        raise HTTPException(404, "tasks.yaml not found in .cod-doc/")

    summary = restate_importer.import_legacy_tasks(
        session,
        yaml_path=yaml_path,
        project_id=project_db_id,
        author="human:web",
    )

    if dry_run:
        session.rollback()
    else:
        session.commit()

    result = summary.to_dict()
    result["dry_run"] = dry_run
    return JSONResponse(result)


@router.post("/p/{slug}/tasks/legacy/archive")
def legacy_tasks_archive(
    request: Request,
    slug: str,
) -> JSONResponse:
    """PCA-410: Rename tasks.yaml → tasks.archived.yaml to mark as archived.

    Idempotent: if already archived returns 200 with archived=true.
    """
    proj = get_project(slug)
    yaml_path = proj.entry.cod_doc_dir / "tasks.yaml"
    archived_path = proj.entry.cod_doc_dir / "tasks.archived.yaml"

    if archived_path.exists():
        return JSONResponse({"archived": True, "message": "Already archived"})

    if not yaml_path.exists():
        raise HTTPException(404, "tasks.yaml not found")

    yaml_path.rename(archived_path)
    return JSONResponse({"archived": True, "path": str(archived_path)})


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
    trace = traces.list_for_task(session, task.row_id)

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
            "trace": [
                {
                    "ts": t.ts,
                    "model": t.model,
                    "kind": t.kind,
                    "input_tokens": t.input_tokens,
                    "output_tokens": t.output_tokens,
                    "total_tokens": t.total_tokens,
                    "duration_ms": t.duration_ms,
                    "tool_calls": t.tool_calls or [],
                    "error": t.error,
                }
                for t in trace
            ],
            "trace_totals": {
                "calls": len(trace),
                "input_tokens": sum(t.input_tokens for t in trace),
                "output_tokens": sum(t.output_tokens for t in trace),
                "duration_ms": sum(t.duration_ms for t in trace),
            },
        },
    )
