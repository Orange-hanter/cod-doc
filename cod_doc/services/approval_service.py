"""ApprovalService — first-class decision gates (PCA-121, proposal 12).

Approvals pause tasks pending a human decision. On resolve the service
returns a :class:`WakeContext` hint that callers can pass to
``run_agent_once`` to immediately resume the requesting agent.

Types: 'plan_review' | 'risky_action' | 'fm_escalation' | 'budget' | 'manual'
Status: 'pending' | 'approved' | 'denied' | 'cancelled' | 'expired'

Invariant: at most one pending approval per task (enforced in ``request``).
A duplicate request auto-cancels the previous one with reason='superseded'.

Public API
----------
- ``request(session, project_id, …)`` → Approval
- ``get(session, project_id, approval_id)`` → Approval | None
- ``list_approvals(session, project_id, …)`` → paginated dict
- ``resolve(session, project_id, approval_id, decision, …)`` → Approval
- ``cancel(session, project_id, approval_id, reason)`` → Approval
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import select, func

from cod_doc.infra.models import (
    ApprovalDocRevisionLinkModel,
    ApprovalModel,
    ApprovalTaskLinkModel,
    TaskModel,
)
from cod_doc.services.run_context import get_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

VALID_TYPES = frozenset({"plan_review", "risky_action", "fm_escalation", "budget", "manual"})
VALID_STATUSES = frozenset({"pending", "approved", "denied", "cancelled", "expired"})
# Default approval TTL per proposal 12 note (48 hours for single-user).
DEFAULT_TTL_HOURS = 48


@dataclass
class Approval:
    row_id: int
    approval_id: str
    project_id: int
    approval_type: str
    status: str
    requested_by: str
    requested_at: datetime
    resolved_by: str | None
    resolved_at: datetime | None
    payload: dict[str, Any]
    expires_at: datetime | None
    decision_comment: str | None
    run_id: str | None
    linked_task_refs: list[str]
    linked_doc_revision_ids: list[str]


def _to_domain(m: ApprovalModel, session: Session) -> Approval:
    task_refs = list(session.execute(
        select(ApprovalTaskLinkModel.task_ref)
        .where(ApprovalTaskLinkModel.approval_id == m.row_id)
        .order_by(ApprovalTaskLinkModel.row_id)
    ).scalars())
    doc_revs = list(session.execute(
        select(ApprovalDocRevisionLinkModel.revision_id)
        .where(ApprovalDocRevisionLinkModel.approval_id == m.row_id)
        .order_by(ApprovalDocRevisionLinkModel.row_id)
    ).scalars())
    return Approval(
        row_id=m.row_id,
        approval_id=m.approval_id,
        project_id=m.project_id,
        approval_type=m.approval_type,
        status=m.status,
        requested_by=m.requested_by,
        requested_at=m.requested_at,
        resolved_by=m.resolved_by,
        resolved_at=m.resolved_at,
        payload=m.payload_json or {},
        expires_at=m.expires_at,
        decision_comment=m.decision_comment,
        run_id=m.run_id,
        linked_task_refs=task_refs,
        linked_doc_revision_ids=doc_revs,
    )


def _approval_to_dict(a: Approval) -> dict[str, Any]:
    return {
        "approval_id": a.approval_id,
        "approval_type": a.approval_type,
        "status": a.status,
        "requested_by": a.requested_by,
        "requested_at": a.requested_at.isoformat() if a.requested_at else None,
        "resolved_by": a.resolved_by,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "payload": a.payload,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
        "decision_comment": a.decision_comment,
        "run_id": a.run_id,
        "linked_task_refs": a.linked_task_refs,
        "linked_doc_revision_ids": a.linked_doc_revision_ids,
    }


def _cancel_existing_pending(
    session: Session,
    project_id: int,
    task_refs: list[str],
    reason: str = "superseded",
) -> None:
    """Cancel any pending approvals already linked to the same tasks."""
    if not task_refs:
        return
    # Find pending approval rows that have at least one link to these tasks.
    existing_links = session.execute(
        select(ApprovalTaskLinkModel.approval_id)
        .where(ApprovalTaskLinkModel.task_ref.in_(task_refs))
    ).scalars()
    pending_ids = set(existing_links)
    if not pending_ids:
        return
    pending = list(session.execute(
        select(ApprovalModel).where(
            ApprovalModel.row_id.in_(pending_ids),
            ApprovalModel.project_id == project_id,
            ApprovalModel.status == "pending",
        )
    ).scalars())
    now = datetime.now(UTC)
    for a in pending:
        a.status = "cancelled"
        a.resolved_at = now
        a.decision_comment = reason
    session.flush()


def request(
    session: Session,
    project_id: int,
    *,
    approval_type: str,
    requested_by: str,
    payload: dict[str, Any] | None = None,
    linked_task_refs: list[str] | None = None,
    linked_doc_revision_ids: list[str] | None = None,
    expires_in_hours: int | None = DEFAULT_TTL_HOURS,
) -> Approval:
    """Create a new approval request.

    If any of the linked tasks already have a pending approval, that approval
    is auto-cancelled with reason='superseded' before creating the new one.
    """
    if approval_type not in VALID_TYPES:
        raise ValueError(f"Invalid approval_type {approval_type!r}. Valid: {sorted(VALID_TYPES)}")

    task_refs = linked_task_refs or []
    doc_revs = linked_doc_revision_ids or []

    # Enforce single-pending-per-task invariant.
    _cancel_existing_pending(session, project_id, task_refs)

    expires_at = (
        datetime.now(UTC) + timedelta(hours=expires_in_hours)
        if expires_in_hours is not None
        else None
    )

    m = ApprovalModel(
        approval_id=str(uuid4()),
        project_id=project_id,
        approval_type=approval_type,
        status="pending",
        requested_by=requested_by,
        payload_json=payload or {},
        expires_at=expires_at,
        run_id=get_current_run_id(),
    )
    session.add(m)
    session.flush()

    for ref in task_refs:
        session.add(ApprovalTaskLinkModel(approval_id=m.row_id, task_ref=ref))
    for rev_id in doc_revs:
        session.add(ApprovalDocRevisionLinkModel(approval_id=m.row_id, revision_id=rev_id))
    session.flush()

    # PCA-913: auto-transition linked in_progress tasks → in_review so the
    # agent knows the task is paused pending human decision.
    from cod_doc.services import task_service as _task_svc
    from cod_doc.domain.entities import TaskStatus

    _IN_PROGRESS_STATUSES = {"in_progress", "in-progress"}
    for ref in task_refs:
        try:
            task = _task_svc.get(session, ref)
            if task is not None and task.status.value in _IN_PROGRESS_STATUSES:
                _task_svc.update_status(
                    session,
                    task_id=ref,
                    new_status=TaskStatus.IN_REVIEW,
                    author=f"approval:{m.approval_id}",
                    reason="approval_requested",
                    strict=False,
                )
        except Exception:
            pass  # best-effort; approval creation must not fail due to this

    return _to_domain(m, session)


def get(session: Session, project_id: int, approval_id: str) -> Approval | None:
    m = session.execute(
        select(ApprovalModel).where(
            ApprovalModel.approval_id == approval_id,
            ApprovalModel.project_id == project_id,
        )
    ).scalar_one_or_none()
    return _to_domain(m, session) if m is not None else None


def list_approvals(
    session: Session,
    project_id: int,
    *,
    status: str | None = None,
    approval_type: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    base = select(ApprovalModel).where(ApprovalModel.project_id == project_id)
    count_q = (
        select(func.count())
        .select_from(ApprovalModel)
        .where(ApprovalModel.project_id == project_id)
    )
    if status is not None:
        base = base.where(ApprovalModel.status == status)
        count_q = count_q.where(ApprovalModel.status == status)
    if approval_type is not None:
        base = base.where(ApprovalModel.approval_type == approval_type)
        count_q = count_q.where(ApprovalModel.approval_type == approval_type)
    if since is not None:
        base = base.where(ApprovalModel.requested_at >= since)
        count_q = count_q.where(ApprovalModel.requested_at >= since)

    total = int(session.execute(count_q).scalar_one() or 0)
    rows = list(session.execute(
        base
        .order_by(ApprovalModel.requested_at.desc(), ApprovalModel.row_id.desc())
        .limit(limit)
        .offset(offset)
    ).scalars())

    return {
        "items": [_approval_to_dict(_to_domain(m, session)) for m in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def resolve(
    session: Session,
    project_id: int,
    approval_id: str,
    *,
    decision: str,
    resolved_by: str,
    comment: str | None = None,
) -> dict[str, Any]:
    """Approve or deny. Returns the updated approval dict + wake_hint for callers.

    ``wake_hint`` carries enough info for the caller to call ``run_agent_once``
    with reason='approval_resolved'. It is None if no tasks were linked.
    """
    if decision not in ("approve", "deny"):
        raise ValueError("decision must be 'approve' or 'deny'")

    m = session.execute(
        select(ApprovalModel).where(
            ApprovalModel.approval_id == approval_id,
            ApprovalModel.project_id == project_id,
        )
    ).scalar_one_or_none()
    if m is None:
        raise LookupError(f"Approval '{approval_id}' not found.")
    if m.status != "pending":
        raise ValueError(f"Approval is already {m.status!r}, cannot resolve.")

    now = datetime.now(UTC)
    m.status = "approved" if decision == "approve" else "denied"
    m.resolved_by = resolved_by
    m.resolved_at = now
    m.decision_comment = comment
    session.flush()

    domain = _to_domain(m, session)

    # Build wake hint — the first linked task gets the wake.
    wake_hint: dict[str, Any] | None = None
    if domain.linked_task_refs:
        first_task = domain.linked_task_refs[0]
        wake_hint = {
            "reason": "approval_resolved",
            "task_id": first_task,
            "approval_id": approval_id,
            "decision": decision,
            "comment": comment,
        }

    return {
        "approval": _approval_to_dict(domain),
        "wake_hint": wake_hint,
    }


def cancel(
    session: Session,
    project_id: int,
    approval_id: str,
    *,
    reason: str,
    cancelled_by: str = "mcp",
) -> Approval:
    m = session.execute(
        select(ApprovalModel).where(
            ApprovalModel.approval_id == approval_id,
            ApprovalModel.project_id == project_id,
        )
    ).scalar_one_or_none()
    if m is None:
        raise LookupError(f"Approval '{approval_id}' not found.")
    if m.status != "pending":
        raise ValueError(f"Only pending approvals can be cancelled; current status={m.status!r}.")

    m.status = "cancelled"
    m.resolved_by = cancelled_by
    m.resolved_at = datetime.now(UTC)
    m.decision_comment = reason
    session.flush()
    return _to_domain(m, session)
