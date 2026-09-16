"""Project task counters from the DB (ADO-076).

``cod-doc project list`` and ``GET /api/projects`` used to read legacy
``tasks.yaml`` (pending/done/failed). The DB is the source of truth for
tracked tasks; yaml is only a fallback when the project has no schema yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import OperationalError, SQLAlchemyError

from cod_doc.core.project import Project
from cod_doc.infra.db import SchemaMismatchError, db_for_entry
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import run_service, task_service

if TYPE_CHECKING:
    from datetime import datetime

    from cod_doc.config import ProjectEntry

_STATUS_ALIASES: dict[str, str] = {
    "pending": "todo",
    "in-progress": "in_progress",
}

CANONICAL_STATUSES: tuple[str, ...] = (
    "backlog",
    "todo",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "cancelled",
)


def canonical_by_status(raw: dict[str, int]) -> dict[str, int]:
    """Fold legacy aliases into the 7-state taxonomy; drop unknown buckets."""
    out = dict.fromkeys(CANONICAL_STATUSES, 0)
    for key, count in raw.items():
        bucket = _STATUS_ALIASES.get(key, key)
        if bucket in out:
            out[bucket] += int(count)
    return out


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _yaml_fallback(entry: ProjectEntry) -> dict[str, Any]:
    stats = Project(entry).stats()
    by_status = canonical_by_status(
        {
            "pending": int(stats.get("pending") or 0),
            "in_progress": int(stats.get("in_progress") or 0),
            "done": int(stats.get("done") or 0),
        }
    )
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_priority": {},
        "status": stats.get("status") or "unknown",
        "last_run": stats.get("last_run"),
        "source": "tasks.yaml",
    }


def stats_for_entry(entry: ProjectEntry) -> dict[str, Any]:
    """DB ``task_summary`` + live ``agent_run`` status; yaml only if DB is missing."""
    try:
        factory, engine = db_for_entry(entry)
    except (SchemaMismatchError, SQLAlchemyError, OSError):
        return _yaml_fallback(entry)

    try:
        with factory() as session:
            project = ProjectRepository(session).get_by_slug(entry.name)
            if project is None or project.row_id is None:
                return _yaml_fallback(entry)
            summary = task_service.summarize_for_project(session, project.row_id)
            by_status = canonical_by_status(summary["by_status"])
            runs = run_service.list_recent(session, project.row_id, limit=20)
            running = any(run["status"] == "running" for run in runs)
            last_run = _iso(runs[0]["started_at"]) if runs else None
            return {
                "total": int(summary["total"]),
                "by_status": by_status,
                "by_priority": summary["by_priority"],
                "status": "running" if running else "idle",
                "last_run": last_run,
                "source": "db",
            }
    except (OperationalError, SQLAlchemyError):
        return _yaml_fallback(entry)
    finally:
        engine.dispose()
