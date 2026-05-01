"""Shared DB session helpers for MCP tool modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def session_factory(project: str):  # type: ignore[no-untyped-def]
    """Return (sessionmaker, ProjectEntry) for the given project slug."""
    from cod_doc.config import Config
    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    cfg = Config.load()
    entry = cfg.get_project(project)
    if not entry:
        raise ValueError(f"Project not found: {project!r}")
    url = resolve_db_url(Path(entry.path))
    engine = make_engine(url)
    return make_session_factory(engine), entry


def require_project_id(session: Any, project: str) -> int:
    """Look up DB project row_id by slug; raise ValueError if missing."""
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project)
    if proj is None or proj.row_id is None:
        raise ValueError(
            f"Project '{project}' not in DB — run 'cod-doc project add' first."
        )
    return proj.row_id


def task_to_dict(t: Any) -> dict[str, Any]:
    return {
        "task_id": t.task_id,
        "title": t.title,
        "status": t.status.value,
        "type": t.type.value,
        "priority": t.priority.value,
        "plan_id": t.plan_id,
        "section_id": t.section_id,
        "description": t.description,
        "acceptance": t.acceptance,
        "created": t.created.isoformat() if t.created else None,
        "last_updated": t.last_updated.isoformat() if t.last_updated else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "completed_commit": t.completed_commit,
    }


def doc_to_dict(d: Any) -> dict[str, Any]:
    return {
        "doc_key": d.doc_key,
        "title": d.title,
        "type": d.type.value,
        "status": d.status.value,
        "sensitivity": d.sensitivity.value,
        "source_of_truth": d.source_of_truth,
        "owner": d.owner,
        "path": d.path,
        "projection_hash": d.projection_hash,
        "last_updated": d.last_updated.isoformat() if d.last_updated else None,
    }


def story_to_dict(s: Any) -> dict[str, Any]:
    return {
        "story_id": s.story_id,
        "persona": s.persona,
        "narrative": s.narrative,
        "status": s.status.value,
        "priority": s.priority.value,
    }
