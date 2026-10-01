"""ApprovalService — first-class decision gates (PCA-121, proposal 12).

Approvals pause tasks pending a human decision. On resolve the service
returns a :class:`WakeContext` hint that callers can pass to
``run_agent_once`` to immediately resume the requesting agent.

Types: 'plan_review' | 'risky_action' | 'fm_escalation' | 'budget' | 'manual' | 'doc_patch'
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

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import func, select

from cod_doc.infra.models import (
    ApprovalDocRevisionLinkModel,
    ApprovalModel,
    ApprovalTaskLinkModel,
)
from cod_doc.services import activity_service
from cod_doc.services.run_context import get_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: ACU-008 (RFC 28 §3.6): готовая правка документации от фонового куратора —
#: одна операция с diff, которую человек одобряет или отклоняет.
DOC_PATCH = "doc_patch"
VALID_TYPES = frozenset(
    {"plan_review", "risky_action", "fm_escalation", "budget", "manual", DOC_PATCH}
)
VALID_STATUSES = frozenset({"pending", "approved", "denied", "cancelled", "expired"})
#: Статус одобрения, ждущего решения. Константа, а не литерал у вызывающих:
#: слово совпадает с легаси-алиасом статуса задачи, и страж
#: `test_task_status_write_canonicalisation` по литералу их не различит.
PENDING = "pending"
# Default approval TTL per proposal 12 note (48 hours for single-user).
DEFAULT_TTL_HOURS = 48
#: ACU-008: сколько дней отказ человека подавляет то же предложение. Без
#: памяти куратор предлагал бы отклонённое каждую ночь; без срока — никогда
#: больше, даже когда документ вокруг уже изменился.
DENIAL_MEMORY_DAYS = 30


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
    task_refs = list(
        session.execute(
            select(ApprovalTaskLinkModel.task_ref)
            .where(ApprovalTaskLinkModel.approval_id == m.row_id)
            .order_by(ApprovalTaskLinkModel.row_id)
        ).scalars()
    )
    doc_revs = list(
        session.execute(
            select(ApprovalDocRevisionLinkModel.revision_id)
            .where(ApprovalDocRevisionLinkModel.approval_id == m.row_id)
            .order_by(ApprovalDocRevisionLinkModel.row_id)
        ).scalars()
    )
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


def to_dict(approval: Approval) -> dict[str, Any]:
    """JSON-safe форма одобрения — та же, что отдают ``list_approvals`` и ``resolve``."""
    return _approval_to_dict(approval)


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
        select(ApprovalTaskLinkModel.approval_id).where(
            ApprovalTaskLinkModel.task_ref.in_(task_refs)
        )
    ).scalars()
    pending_ids = set(existing_links)
    if not pending_ids:
        return
    pending = list(
        session.execute(
            select(ApprovalModel).where(
                ApprovalModel.row_id.in_(pending_ids),
                ApprovalModel.project_id == project_id,
                ApprovalModel.status == "pending",
            )
        ).scalars()
    )
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

    activity_service.emit_for_write(
        session,
        project_id,
        "approval.requested",
        requested_by,
        scope_kind="approval",
        scope_id=m.approval_id,
        payload={
            "approval_type": approval_type,
            "linked_task_refs": task_refs,
            "linked_doc_revision_ids": doc_revs,
        },
        summary=f"Approval {m.approval_id} requested ({approval_type})",
    )

    # PCA-913: auto-transition linked in_progress tasks → in_review so the
    # agent knows the task is paused pending human decision.
    from cod_doc.domain.entities import TaskStatus
    from cod_doc.services import task_service as _task_svc

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


@dataclass(slots=True, frozen=True)
class DocPatchRequest:
    """Итог :func:`request_doc_patch`.

    ``outcome``: ``created`` — новый approval; ``duplicate`` — такой же уже
    ждёт решения, возвращён он; ``suppressed`` — такой же отклонён меньше
    :data:`DENIAL_MEMORY_DAYS` назад, approval нет.
    """

    outcome: str
    fingerprint: str
    approval: Approval | None


def doc_patch_fingerprint(op: str, args: dict[str, Any]) -> str:
    """Отпечаток предложения: операция и её аргументы, без diff и обоснования.

    Diff и текст обоснования модель формулирует каждый раз по-разному; сама
    правка — нет. Ключи сортируются, чтобы порядок в словаре не делал из
    одного предложения два.
    """
    canonical = json.dumps(
        {"op": op, "args": args}, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def request_doc_patch(
    session: Session,
    project_id: int,
    *,
    op: str,
    args: dict[str, Any],
    diff: str,
    rationale: str,
    base_revision_id: str | None = None,
    requested_by: str = "agent:curator",
    now: datetime | None = None,
) -> DocPatchRequest:
    """ACU-008 (RFC 28 §3.3 п.4–5, §3.6): предложить правку документации.

    Payload: ``{op, args, base_revision_id, fingerprint, diff, rationale,
    run_id}``. ``base_revision_id`` — голова, на которую рассчитана правка:
    разойдётся к моменту одобрения — исполнитель (ACU-009) правку не
    применит. Повтор того же предложения не плодит approval: ждущий — тот
    же, недавно отклонённый — молчит. ``now`` — для тестов памяти отказа.
    """
    if not op.strip():
        raise ValueError("doc_patch: op обязателен")
    if not diff.strip():
        raise ValueError("doc_patch: пустой diff — предлагать нечего")
    fingerprint = doc_patch_fingerprint(op, args)
    moment = now or datetime.now(UTC)
    remembered_since = moment - timedelta(days=DENIAL_MEMORY_DAYS)

    same = session.execute(
        select(ApprovalModel).where(
            ApprovalModel.project_id == project_id,
            ApprovalModel.approval_type == DOC_PATCH,
            ApprovalModel.status.in_(("pending", "denied")),
        )
    ).scalars()
    for model in same:
        if (model.payload_json or {}).get("fingerprint") != fingerprint:
            continue
        if model.status == "pending":
            return DocPatchRequest("duplicate", fingerprint, _to_domain(model, session))
        if model.resolved_at is not None and _as_utc(model.resolved_at) > remembered_since:
            return DocPatchRequest("suppressed", fingerprint, None)

    approval = request(
        session,
        project_id,
        approval_type=DOC_PATCH,
        requested_by=requested_by,
        payload={
            "op": op,
            "args": args,
            "base_revision_id": base_revision_id,
            "fingerprint": fingerprint,
            "diff": diff,
            "rationale": rationale,
            "run_id": get_current_run_id(),
        },
    )
    return DocPatchRequest("created", fingerprint, approval)


def _as_utc(moment: datetime) -> datetime:
    """SQLite отдаёт время без зоны, хоть колонка и timezone=True."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


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
    rows = list(
        session.execute(
            base.order_by(ApprovalModel.requested_at.desc(), ApprovalModel.row_id.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )

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
    if m.approval_type == DOC_PATCH and decision == "approve":
        stale = _doc_patch_stale_reason(session, project_id, m.payload_json or {})
        if stale is not None:
            return _expire_stale(session, project_id, m, resolved_by=resolved_by, reason=stale)

    m.status = "approved" if decision == "approve" else "denied"
    m.resolved_by = resolved_by
    m.resolved_at = now
    m.decision_comment = comment
    session.flush()

    activity_service.emit_for_write(
        session,
        project_id,
        "approval.resolved",
        resolved_by,
        scope_kind="approval",
        scope_id=approval_id,
        payload={"decision": decision, "comment": comment},
        summary=f"Approval {approval_id} {decision}d",
    )

    # ACU-009: одобренный doc_patch исполняется здесь же, в транзакции
    # решения. Исключение в операции откатывает и само одобрение — approval
    # не может остаться `approved` с неприменённой правкой.
    applied: dict[str, Any] | None = None
    if m.approval_type == DOC_PATCH and decision == "approve":
        from cod_doc.services import curator_ops

        payload = m.payload_json or {}
        op = curator_ops.require(str(payload.get("op", "")))
        applied = op.apply(session, project_id, dict(payload.get("args") or {}), resolved_by)

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
        "applied": applied,
    }


def _doc_patch_stale_reason(
    session: Session, project_id: int, payload: dict[str, Any]
) -> str | None:
    """Почему doc_patch устарел, или None. Незнакомая операция — ошибка сразу.

    Сверяется голова ревизий предмета правки с той, на которую правка
    рассчитана. ``base_revision_id`` пуст — предлагавший сверку не просил.
    """
    from cod_doc.services import curator_ops

    op = curator_ops.require(str(payload.get("op", "")))
    base = payload.get("base_revision_id")
    if base is None:
        return None
    head = op.head(session, project_id, dict(payload.get("args") or {}))
    if head != base:
        return f"stale_base: правка рассчитана на {base}, голова сейчас {head}"
    return None


def _expire_stale(
    session: Session,
    project_id: int,
    m: ApprovalModel,
    *,
    resolved_by: str,
    reason: str,
) -> dict[str, Any]:
    """Одобрение пришло к устаревшей правке: approval истекает, ничего не пишется."""
    m.status = "expired"
    m.resolved_by = resolved_by
    m.resolved_at = datetime.now(UTC)
    m.decision_comment = reason
    session.flush()
    activity_service.emit_for_write(
        session,
        project_id,
        "approval.expired",
        resolved_by,
        scope_kind="approval",
        scope_id=m.approval_id,
        payload={"reason": reason},
        summary=f"Approval {m.approval_id} expired: {reason}",
    )
    return {
        "approval": _approval_to_dict(_to_domain(m, session)),
        "wake_hint": None,
        "applied": None,
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

    activity_service.emit_for_write(
        session,
        project_id,
        "approval.cancelled",
        cancelled_by,
        scope_kind="approval",
        scope_id=approval_id,
        payload={"reason": reason},
        summary=f"Approval {approval_id} cancelled",
    )

    return _to_domain(m, session)
