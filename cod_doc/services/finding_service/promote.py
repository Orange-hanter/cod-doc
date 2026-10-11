"""Promote a finding into a task according to a routine's ``on_finding`` policy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import Priority, TaskType, actor_kind_for_author
from cod_doc.infra.models import FindingModel, PlanModel, PlanSectionModel
from cod_doc.infra.repositories import PlanSectionRepository
from cod_doc.services import activity_service, plan_service, search_service, task_service
from cod_doc.services.finding_service.queries import get_finding

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
    author: str = "system:finding_service",
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

    ``plan_id``/``section_id`` — сырые ``row_id``, поэтому принадлежность
    проекту проверяется здесь, а не только в :func:`promote_finding_by_ref`:
    иначе прямой вызов создавал бы задачу в плане чужого проекта.
    """
    finding = session.get(FindingModel, finding_id)
    if finding is None:
        raise ValueError(f"finding #{finding_id} not found")
    if finding.project_id != project_id:
        raise ValueError("finding does not belong to the given project")

    if on_finding == "comment_only":
        return {"promoted": False, "finding_id": finding_id}

    if on_finding == "create_task":
        plan = session.get(PlanModel, plan_id)
        if plan is None or plan.project_id != project_id:
            raise ValueError(f"plan #{plan_id} does not belong to the given project")
        section = session.get(PlanSectionModel, section_id)
        if section is None or section.plan_id != plan_id:
            raise ValueError(f"section #{section_id} does not belong to plan #{plan_id}")
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
            actor_kind=actor_kind_for_author(author),
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
        search_service.index_finding(session, finding)
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
            project_id=project_id,
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
            actor_kind=actor_kind_for_author(author),
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
        search_service.index_finding(session, finding)
        return {
            "promoted": True,
            "finding_id": finding_id,
            "task_id": finding.promoted_task_id,
            "on_finding": on_finding,
        }

    raise ValueError(f"unknown on_finding policy: {on_finding!r}")


def promote_finding_by_ref(
    session: Session,
    *,
    project_id: int,
    finding_uid: str,
    plan_scope: str,
    section_letter: str,
    on_finding: str,
    author: str = "system:finding_service",
    id_prefix: str = "FND",
) -> dict[str, Any]:
    """:func:`promote_finding` по внешним ключам: ``finding_uid``, scope плана, буква секции.

    Резолв и проверка существования живут здесь, а не в поверхности: план
    ищется в проекте (scope уникален только в нём, STO-023), буква секции —
    без учёта регистра. Промах — ``ValueError`` с текстом для агента.
    """
    plan = plan_service.get_by_scope(session, plan_scope, project_id=project_id)
    if plan is None or plan.row_id is None:
        raise ValueError(f"Plan '{plan_scope}' not found.")
    sections = PlanSectionRepository(session).list_for_plan(plan.row_id)
    section = next((s for s in sections if s.letter.upper() == section_letter.upper()), None)
    if section is None or section.row_id is None:
        letters = ", ".join(s.letter for s in sections)
        raise ValueError(f"Section '{section_letter}' not found. Available: {letters}")

    finding = get_finding(session, project_id, finding_uid)
    if finding is None:
        raise ValueError(f"finding '{finding_uid}' not found")

    return promote_finding(
        session,
        project_id=project_id,
        finding_id=finding["finding_id"],
        on_finding=on_finding,
        plan_id=plan.row_id,
        section_id=section.row_id,
        author=author,
        id_prefix=id_prefix,
    )
