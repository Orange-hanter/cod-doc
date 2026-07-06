"""Shared kanban board helpers for tasks list page + HTMX live-refresh fragments."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from cod_doc.domain.entities import TaskStatus
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service as tasks

# Kanban columns in workflow order. Tuple: (column_key, label_i18n_key, icon, statuses).
KANBAN_COLS: list[tuple[str, str, str, set[str]]] = [
    ("todo", "kanban.todo", "○", {"backlog", "todo", "pending"}),
    ("in_progress", "kanban.in_progress", "◐", {"in_progress", "in-progress"}),
    ("in_review", "kanban.in_review", "◔", {"in_review"}),
    ("blocked", "kanban.blocked", "✕", {"blocked"}),
    ("done", "kanban.done", "●", {"done"}),
    ("cancelled", "kanban.cancelled", "—", {"cancelled"}),
]

TYPE_GLYPHS: dict[str, str] = {
    "feature": "F",
    "bug": "B",
    "refactor": "R",
    "test": "T",
    "docs": "D",
    "chore": "C",
}

PRIO_RANK: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def column_for(status: str) -> str | None:
    """Map a task status value to its kanban column key."""
    for key, _label, _icon, members in KANBAN_COLS:
        if status in members:
            return key
    return None


def load_task_rows(session: Session, project_db_id: int) -> list[dict[str, Any]]:
    """Load display-ready task dicts for kanban / stats."""
    plans_models = plans.list_for_project(session, project_db_id)
    scope_by_pid: dict[int, str] = {}
    sections_by_pid: dict[int, dict[int, dict[str, str]]] = {}
    for p in plans_models:
        if p.row_id is None:
            continue
        scope_by_pid[p.row_id] = p.scope
        sects = plans.list_sections(session, p.row_id)
        sections_by_pid[p.row_id] = {
            s.row_id: {"letter": s.letter or "", "title": s.title}
            for s in sects
            if s.row_id is not None
        }

    rows: list[dict[str, Any]] = []
    for t in tasks.list_for_project(session, project_db_id):
        plan_scope = scope_by_pid.get(t.plan_id, "")
        sect = sections_by_pid.get(t.plan_id, {}).get(t.section_id, {})
        has_ac = bool(t.acceptance and t.acceptance.strip())
        has_desc = bool(t.description and t.description.strip())
        rows.append(
            {
                "task_id": t.task_id,
                "title": t.title,
                "status": t.status.value,
                "type": t.type.value,
                "type_glyph": TYPE_GLYPHS.get(t.type.value, "?"),
                "priority": t.priority.value,
                "plan_id": t.plan_id,
                "plan_scope": plan_scope,
                "section_letter": sect.get("letter", ""),
                "section_title": sect.get("title", ""),
                "has_acceptance": has_ac,
                "has_description": has_desc,
                "blocked_reason": t.blocked_reason or "",
            }
        )
    return rows


def compute_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    stats = {
        "total": total,
        "done": sum(1 for r in rows if r["status"] == "done"),
        "in_progress": sum(1 for r in rows if r["status"] in ("in_progress", "in-progress")),
        "blocked": sum(1 for r in rows if r["status"] == "blocked"),
        "in_review": sum(1 for r in rows if r["status"] == "in_review"),
        "missing_ac": sum(1 for r in rows if not r["has_acceptance"] and r["status"] != "done"),
        "critical_open": sum(
            1 for r in rows if r["priority"] == "critical" and r["status"] != "done"
        ),
    }
    stats["pct_done"] = int(stats["done"] / total * 100) if total else 0
    return stats


def build_columns(
    rows: list[dict[str, Any]],
    *,
    status_filter: TaskStatus | None = None,
) -> list[dict[str, Any]]:
    """Bucket rows into kanban columns, priority-sorted within each."""
    columns: list[dict[str, Any]] = []
    for key, label_key, icon, members in KANBAN_COLS:
        col_tasks = [r for r in rows if column_for(r["status"]) == key]
        col_tasks.sort(key=lambda r: (PRIO_RANK.get(r["priority"], 99), r["task_id"]))
        columns.append(
            {
                "key": key,
                "label_key": label_key,
                "icon": icon,
                "count": len(col_tasks),
                "tasks": col_tasks,
                "collapsed_default": key in ("done", "cancelled"),
                "highlighted": status_filter is not None and status_filter.value in members,
            }
        )
    return columns


def board_refresh_url(
    project_name: str,
    *,
    plan: str = "",
    status: str = "",
) -> str:
    """HTMX refresh URL for the live tasks board region."""
    params: list[str] = []
    if plan:
        params.append(f"plan={plan}")
    if status:
        params.append(f"status={status}")
    qs = ("?" + "&".join(params)) if params else ""
    return f"/p/{project_name}/frag/tasks/board{qs}"
