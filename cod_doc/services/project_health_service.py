"""Project health read model for API, Web, and automation consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from cod_doc.infra.models import LinkModel
from cod_doc.services import projection_service
from cod_doc.services import routine_service as routines

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def build_project_health(
    session: Session,
    project_id: int,
    *,
    root_path: Path,
) -> dict[str, Any]:
    """Return a compact, read-only health summary for a DB-backed project."""
    drift = projection_service.detect_project_drift(
        session,
        project_id,
        root_path=root_path,
    )
    links_total = _count_links(session, project_id, unresolved_only=False)
    unresolved_links = _count_links(session, project_id, unresolved_only=True)
    doc_drift_routine = _doc_drift_routine(session, project_id)

    status = "ok"
    if doc_drift_routine["last_run"] and doc_drift_routine["last_run"]["status"] == "failed":
        status = "error"
    elif drift.problem_count > 0 or unresolved_links > 0:
        status = "warning"

    return {
        "status": status,
        "db_available": True,
        "documents": {
            "total": drift.total_docs,
            "drift_counts": dict(drift.counts),
            "drift_problem_count": drift.problem_count,
            "drift_issues": [
                {
                    "doc_key": item.doc_key,
                    "path": item.path,
                    "status": item.report.status.value,
                    "document_id": item.report.document_id,
                }
                for item in drift.issues
            ],
        },
        "links": {
            "total": links_total,
            "unresolved": unresolved_links,
        },
        "routines": {
            "doc_drift": doc_drift_routine,
        },
    }


def uninitialized_project_health() -> dict[str, Any]:
    """Shape-compatible health payload when `.cod-doc/state.db` is absent."""
    return {
        "status": "uninitialized",
        "db_available": False,
        "documents": {
            "total": 0,
            "drift_counts": {},
            "drift_problem_count": None,
            "drift_issues": [],
        },
        "links": {
            "total": 0,
            "unresolved": None,
        },
        "routines": {
            "doc_drift": {
                "configured": False,
                "name": None,
                "enabled": False,
                "last_run": None,
            },
        },
    }


def doc_drift_badge(health: dict[str, Any], *, href: str | None = None) -> dict[str, Any]:
    """Map project health into the small Web overview badge contract."""
    if not health.get("db_available"):
        return {
            "level": "muted",
            "label": "no db",
            "findings": None,
            "last_run_at": None,
            "href": None,
        }

    routine = health["routines"]["doc_drift"]
    last_run = routine.get("last_run")
    problem_count = health["documents"]["drift_problem_count"]
    if last_run and last_run["status"] == "failed":
        return {
            "level": "error",
            "label": "failed",
            "findings": last_run["findings_count"],
            "last_run_at": last_run["started_at"],
            "href": href,
        }
    if problem_count and problem_count > 0:
        return {
            "level": "warning",
            "label": "findings",
            "findings": problem_count,
            "last_run_at": last_run["started_at"] if last_run else None,
            "href": href,
        }
    if routine["configured"]:
        return {
            "level": "success",
            "label": "clean",
            "findings": 0,
            "last_run_at": last_run["started_at"] if last_run else None,
            "href": href,
        }
    return {
        "level": "muted",
        "label": "not configured",
        "findings": None,
        "last_run_at": None,
        "href": None,
    }


def _count_links(session: Session, project_id: int, *, unresolved_only: bool) -> int:
    stmt = select(func.count(LinkModel.row_id)).where(LinkModel.project_id == project_id)
    if unresolved_only:
        stmt = stmt.where(LinkModel.resolved.is_(False))
    return int(session.execute(stmt).scalar_one())


def _doc_drift_routine(session: Session, project_id: int) -> dict[str, Any]:
    routine = next(
        (
            routine
            for routine in routines.list_routines(session, project_id)
            if routine.check_name == "doc_drift"
        ),
        None,
    )
    if routine is None:
        return {
            "configured": False,
            "name": None,
            "enabled": False,
            "last_run": None,
        }

    history = routines.history(session, project_id, routine.name, limit=1)
    last_run = history[0] if history else None
    return {
        "configured": True,
        "name": routine.name,
        "enabled": routine.enabled,
        "last_run": None
        if last_run is None
        else {
            "status": last_run.status,
            "findings_count": last_run.findings_count,
            "started_at": last_run.started_at,
            "finished_at": last_run.finished_at,
            "error": last_run.error,
            "run_id": last_run.run_id,
        },
    }
