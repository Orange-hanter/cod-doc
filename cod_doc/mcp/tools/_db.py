"""Shared DB session helpers for MCP tool modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import ProjectEntry


def _resolve(project: str | None) -> str:
    """Cycle-4: resolve ``project`` (or fall back to workspace default).

    Raises ValueError with a hint when neither argument nor default is set —
    so a fresh agent sees actionable guidance, not a None-key lookup.
    """
    from cod_doc.mcp.tools._workspace import resolve as _ws_resolve

    return _ws_resolve(project)


def session_factory(project: str | None) -> tuple[sessionmaker[Session], ProjectEntry]:
    """Return (sessionmaker, ProjectEntry) for the given project slug.

    Cycle-4: ``project=None`` triggers fallback to
    :func:`cod_doc.mcp.tools._workspace.get` (set via ``set_default_project``).
    Falsy value with no default raises ValueError with a hint.
    """
    from cod_doc.config import Config
    from cod_doc.infra.db import db_for_entry

    name = _resolve(project)
    cfg = Config.load()
    entry = cfg.get_project(name)
    if not entry:
        raise ValueError(f"Project not found: {name!r}")
    factory, _engine = db_for_entry(entry)
    return factory, entry


def require_project_id(session: Any, project: str | None) -> int:
    """Look up DB project row_id by slug; raise ValueError if missing.

    Cycle-4: ``project=None`` falls back to workspace default.
    """
    from cod_doc.infra.repositories import ProjectRepository

    name = _resolve(project)
    proj = ProjectRepository(session).get_by_slug(name)
    if proj is None or proj.row_id is None:
        raise ValueError(f"Project '{name}' not in DB — run 'cod-doc project add' first.")
    return proj.row_id


def task_to_dict(t: Any, session: Any | None = None) -> dict[str, Any]:
    """Render a Task domain object as a dict for MCP/JSON return.

    When ``session`` is provided, blocked_by/affects_files/story_id are
    populated from related tables (`dependency`, `affected_file`,
    `story_link`). Without session, they default to empty (legacy behaviour
    for callers that don't care about graph relations).
    """
    result = {
        "task_id": t.task_id,
        "title": t.title,
        "status": t.status.value,
        "type": t.type.value,
        "priority": t.priority.value,
        "plan_id": t.plan_id,
        "section_id": t.section_id,
        "description": t.description,
        "acceptance": t.acceptance,
        "blocked_reason": t.blocked_reason,
        "blocked_by": [],
        "affects_files": [],
        "story_id": None,
        "created": t.created.isoformat() if t.created else None,
        "last_updated": t.last_updated.isoformat() if t.last_updated else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "completed_commit": t.completed_commit,
    }
    if session is not None and getattr(t, "row_id", None) is not None:
        from sqlalchemy import select

        from cod_doc.infra.models import (
            AffectedFileModel,
            DependencyModel,
            StoryLinkModel,
            TaskModel,
            UserStoryModel,
        )

        result["blocked_by"] = list(
            session.execute(
                select(TaskModel.task_id)
                .join(DependencyModel, DependencyModel.to_task_id == TaskModel.row_id)
                .where(
                    DependencyModel.from_task_id == t.row_id,
                    DependencyModel.kind == "blocks",
                )
                .order_by(TaskModel.task_id)
            ).scalars()
        )
        result["affects_files"] = list(
            session.execute(
                select(AffectedFileModel.path)
                .where(AffectedFileModel.task_id == t.row_id)
                .order_by(AffectedFileModel.path)
            ).scalars()
        )
        story = session.execute(
            select(UserStoryModel.story_id)
            .join(StoryLinkModel, StoryLinkModel.story_id == UserStoryModel.row_id)
            .where(
                StoryLinkModel.to_kind == "task",
                StoryLinkModel.to_ref == t.task_id,
            )
            .limit(1)
        ).scalar_one_or_none()
        result["story_id"] = story
    return result


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
