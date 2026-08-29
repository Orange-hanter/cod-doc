"""Serializers: domain objects → JSON-able dicts for MCP/API/CLI surfaces.

Lives in the services layer so any surface (MCP tools, REST API, CLI, agent
service) can render a domain object without the services layer importing
upwards (ADO-041 / audit finding M6 — services must not depend on
``cod_doc.mcp.*``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Task


def task_to_dict(t: Task, session: Session | None = None) -> dict[str, Any]:
    """Render a Task domain object as a dict for MCP/JSON return.

    When ``session`` is provided, blocked_by/affects_files/story_id are
    populated from related tables (`dependency`, `affected_file`,
    `story_link`). Without session, they default to empty (legacy behaviour
    for callers that don't care about graph relations).
    """
    result: dict[str, Any] = {
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
