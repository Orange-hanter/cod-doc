"""Promote a finding into a task according to a routine's ``on_finding`` policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.models import FindingModel
from cod_doc.services import activity_service, task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


FINDING_STATUS_PROMOTED = "promoted"


def _severity_to_priority(severity: str) -> Priority:
    """Map external severity to a cod-doc task priority."""
    mapping = {
        "critical": Priority.CRITICAL,
        "major": Priority.HIGH,
        "minor": Priority.MEDIUM,
        "info": Priority.LOW,
    }
    return mapping.get(severity.lower(), Priority.MEDIUM)


def promote_finding(
    session: Session,
    *,
    project_id: int,
    finding_id: int,
    on_finding: str,
    plan_id: int,
    section_id: int,
    author: str = "finding_service",
    id_prefix: str = "FND",
) -> dict[str, Any]:
    """Promote a finding to a task based on ``on_finding`` policy.

    Policies (from :data:`cod_doc.services.routine_service.VALID_ON_FINDING`):

    - ``create_task``: create a new task, mark the finding ``promoted`` and
      store ``promoted_task_id``.
    - ``update_existing_task``: update the task already referenced by
      ``finding.promoted_task_id``; raises ``ValueError`` if none is set.
    - ``comment_only``: no-op; returns ``{"promoted": False}``.

    Emits an ``activity_event`` when a task is created or updated.
    """
    finding = session.get(FindingModel, finding_id)
    if finding is None:
        raise ValueError(f"finding #{finding_id} not found")
    if finding.project_id != project_id:
        raise ValueError("finding does not belong to the given project")

    if on_finding == "comment_only":
        return {"promoted": False, "finding_id": finding_id}

    if on_finding == "create_task":
        task = task_service.create(
            session,
            project_id=project_id,
            plan_id=plan_id,
            section_id=section_id,
            title=finding.title,
            type=TaskType.BUG,
            priority=_severity_to_priority(finding.severity),
            description=finding.body,
            author=author,
            id_prefix=id_prefix,
            allow_duplicate=True,
        )
        finding.promoted_task_id = task.task_id
        finding.status = FINDING_STATUS_PROMOTED
        session.flush()

        activity_service.emit(
            session,
            project_id,
            "finding.promoted",
            actor_kind="system",
            actor_id=author,
            scope_kind="finding",
            scope_id=finding.finding_uid,
            payload={
                "finding_id": finding_id,
                "promoted_task_id": task.task_id,
                "on_finding": on_finding,
                "source": finding.source,
            },
            summary=f"Finding {finding.finding_uid} promoted to task {task.task_id}",
        )
        return {
            "promoted": True,
            "finding_id": finding_id,
            "task_id": task.task_id,
            "on_finding": on_finding,
        }

    if on_finding == "update_existing_task":
        if not finding.promoted_task_id:
            raise ValueError(f"finding #{finding_id} has no promoted_task_id to update")
        task_service.update_description(
            session,
            task_id=finding.promoted_task_id,
            new_description=finding.body or "",
            author=author,
            reason="finding_promote_update",
        )
        finding.status = FINDING_STATUS_PROMOTED
        session.flush()

        activity_service.emit(
            session,
            project_id,
            "finding.promoted",
            actor_kind="system",
            actor_id=author,
            scope_kind="finding",
            scope_id=finding.finding_uid,
            payload={
                "finding_id": finding_id,
                "promoted_task_id": finding.promoted_task_id,
                "on_finding": on_finding,
                "source": finding.source,
            },
            summary=(
                f"Finding {finding.finding_uid} updated existing task {finding.promoted_task_id}"
            ),
        )
        return {
            "promoted": True,
            "finding_id": finding_id,
            "task_id": finding.promoted_task_id,
            "on_finding": on_finding,
        }

    raise ValueError(f"unknown on_finding policy: {on_finding!r}")
