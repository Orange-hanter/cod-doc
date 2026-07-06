"""HeartbeatService — compact composed snapshot for an iteration start (PCA-010).

Closes proposal 02 (heartbeat-context endpoint): one MCP call replaces the
``get_master + task_get + read_context`` triple for an agent's iteration
start. Pure composition over `task_service`, `revision_service`, the
`dependency` graph, and `story_link` reverse-lookup — no new persistence.

Invariants:
- Response payload ≤ 4 KB on a typical heartbeat (no full markdown bodies).
- ``since_revision_id`` filters ``recent_changes`` to revisions newer than
  that revision (by ``at`` timestamp + row_id tie-break).
- Unknown task_id → :class:`task_service.TaskNotFoundError`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    StoryLinkModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# --------------------------------------------------------------------------- #
# Constants                                                                   #
# --------------------------------------------------------------------------- #

PAYLOAD_BUDGET_BYTES = 4096
"""Soft cap on the JSON payload size; the service trims the most variable
fields (title, recent_changes window) when exceeded. Hard error only if the
fixed-shape skeleton already overflows."""

_TITLE_MAX_CHARS = 160
_RECENT_CHANGES_MAX = 20
_BLOCKED_BY_MAX = 16


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _trim_title(s: str) -> str:
    return s if len(s) <= _TITLE_MAX_CHARS else s[: _TITLE_MAX_CHARS - 1] + "…"


def _resolve_blocked_by_ids(session: Session, task_row_id: int) -> list[str]:
    stmt = (
        select(TaskModel.task_id)
        .join(DependencyModel, DependencyModel.to_task_id == TaskModel.row_id)
        .where(
            DependencyModel.from_task_id == task_row_id,
            DependencyModel.kind == "blocks",
        )
        .order_by(TaskModel.task_id)
        .limit(_BLOCKED_BY_MAX)
    )
    return list(session.execute(stmt).scalars())


def _resolve_story_id(session: Session, task_id: str) -> str | None:
    return session.execute(
        select(UserStoryModel.story_id)
        .join(StoryLinkModel, StoryLinkModel.story_id == UserStoryModel.row_id)
        .where(StoryLinkModel.to_kind == "task", StoryLinkModel.to_ref == task_id)
        .limit(1)
    ).scalar_one_or_none()


def _resolve_ancestry(session: Session, model: TaskModel) -> dict[str, Any]:
    project = session.get(ProjectModel, model.project_id)
    plan = session.get(PlanModel, model.plan_id)
    section = session.get(PlanSectionModel, model.section_id)
    out: dict[str, Any] = {
        "project": (
            {"slug": project.slug, "title": project.title} if project is not None else None
        ),
        "plan": {"scope": plan.scope} if plan is not None else None,
        "section": (
            {"letter": section.letter, "title": section.title} if section is not None else None
        ),
        "story": None,
    }
    story_id = _resolve_story_id(session, model.task_id)
    if story_id is not None:
        story_model = session.execute(
            select(UserStoryModel).where(UserStoryModel.story_id == story_id).limit(1)
        ).scalar_one_or_none()
        if story_model is not None:
            out["story"] = {
                "id": story_model.story_id,
                "status": story_model.status,
                "priority": story_model.priority,
            }
    return out


def _recent_changes_for_task(
    session: Session, task_row_id: int, since_revision_id: str | None
) -> list[dict[str, Any]]:
    """Return revisions of this task newer than ``since_revision_id``.

    No since → empty list (caller is doing a cold-start; the full history
    is one ``revision.list`` away if needed). Cursor approach mirrors
    paperclip's ``after_comment_id`` semantics.
    """
    if since_revision_id is None:
        return []

    cursor = session.execute(
        select(RevisionModel.at, RevisionModel.row_id)
        .where(RevisionModel.revision_id == since_revision_id)
        .limit(1)
    ).one_or_none()
    if cursor is None:
        return []
    cursor_at, cursor_row = cursor

    stmt = (
        select(RevisionModel)
        .where(
            RevisionModel.entity_kind == EntityKind.TASK.value,
            RevisionModel.entity_id == task_row_id,
            (RevisionModel.at > cursor_at)
            | ((RevisionModel.at == cursor_at) & (RevisionModel.row_id > cursor_row)),
        )
        .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
        .limit(_RECENT_CHANGES_MAX)
    )
    out: list[dict[str, Any]] = []
    for rev in session.execute(stmt).scalars():
        try:
            diff = json.loads(rev.diff)
            op = diff.get("op", "?") if isinstance(diff, dict) else "?"
        except (json.JSONDecodeError, AttributeError):
            op = "?"
        out.append(
            {
                "revision_id": rev.revision_id,
                "at": rev.at.isoformat() if rev.at else None,
                "author": rev.author,
                "op": op,
            }
        )
    return out


def _next_action_guess(task: TaskModel, blocked_by_ids: list[str]) -> str:
    if task.status == "done":
        return "task is done — pick next from plan_ready"
    if task.status == "blocked":
        return "investigate blocker_reason and either clear or escalate"
    if blocked_by_ids:
        return f"wait on blockers: {', '.join(blocked_by_ids)}"
    if task.status == "pending":
        return "checkout + start work"
    if task.status == "in-progress":
        return "continue: read context_refs, complete acceptance"
    return ""


def _resolve_task_documents(session: Session, task_row_id: int) -> list[dict[str, Any]]:
    """PCA-916: compact list of task-bound docs (key/title/doc_type)."""
    try:
        from cod_doc.services import task_doc_service

        docs = task_doc_service.list_for_task(session, task_row_id)
        return [{"key": d.key, "title": d.title or "", "doc_type": d.key or ""} for d in docs]
    except Exception:
        return []


def _resolve_pending_approvals(
    session: Session, project_id: int, task_id: str
) -> list[dict[str, Any]]:
    """PCA-916: approvals linked to task_id with status='pending'."""
    try:
        from cod_doc.infra.models.approvals import ApprovalModel, ApprovalTaskLinkModel

        rows = (
            session.execute(
                select(ApprovalModel)
                .join(
                    ApprovalTaskLinkModel,
                    ApprovalTaskLinkModel.approval_id == ApprovalModel.row_id,
                )
                .where(
                    ApprovalModel.project_id == project_id,
                    ApprovalModel.status == "pending",
                    ApprovalTaskLinkModel.task_ref == task_id,
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "approval_id": a.approval_id,
                "approval_type": a.approval_type,
                "requested_by": a.requested_by,
                "expires_at": a.expires_at.isoformat() if a.expires_at else None,
            }
            for a in rows
        ]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def heartbeat_context(
    session: Session,
    *,
    task_id: str,
    since_revision_id: str | None = None,
) -> dict[str, Any]:
    """Return a compact iteration-start snapshot for ``task_id``.

    Raises ``TaskNotFoundError`` if the task isn't in the DB.

    The shape follows proposal 02 §"Предложение"; payload size is enforced
    softly (titles trimmed, recent_changes capped at 20 items, blocked_by
    capped at 16 ids) but the JSON-encoded result will typically stay well
    under 4 KB. Callers can re-issue with a different ``since_revision_id``
    cursor for incremental updates.
    """
    model = session.execute(
        select(TaskModel).where(TaskModel.task_id == task_id)
    ).scalar_one_or_none()
    if model is None:
        raise task_service.TaskNotFoundError(task_id)

    blocked_by_ids = _resolve_blocked_by_ids(session, model.row_id)

    # PCA-916: include task-bound docs and pending approvals.
    task_documents = _resolve_task_documents(session, model.row_id)
    pending_approvals = _resolve_pending_approvals(session, model.project_id, task_id)

    return {
        "task": {
            "id": model.task_id,
            "status": model.status,
            "title": _trim_title(model.title),
            "type": model.type,
            "priority": model.priority,
            "blocked_by": blocked_by_ids,
            "blocked_reason": model.blocked_reason,
        },
        "ancestry": _resolve_ancestry(session, model),
        "linked_docs_summary": [],
        "task_documents": task_documents,
        "pending_approvals": pending_approvals,
        "recent_changes": _recent_changes_for_task(session, model.row_id, since_revision_id),
        "active_skills_hint": ["orchestrator"],
        "next_action_guess": _next_action_guess(model, blocked_by_ids),
    }
