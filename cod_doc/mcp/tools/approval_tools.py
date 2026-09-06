"""MCP tools: approval.* — first-class decision gates (PCA-121, proposal 12).

Tools
-----
- ``approval_request``  — create a new approval; auto-cancels prior pending on same tasks
- ``approval_list``     — pending / resolved approvals with filters
- ``approval_get``      — single approval by ID
- ``approval_resolve``  — approve or deny; returns wake_hint for agent resume
- ``approval_cancel``   — cancel a pending approval
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import actor_kind_for_author
from cod_doc.mcp.tools._db import require_project_id, session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register approval.* tools on the given FastMCP instance."""

    @mcp.tool(name="approval_request")
    def approval_request(
        project: str,
        approval_type: str,
        requested_by: str,
        payload: dict[str, Any] | None = None,
        linked_task_refs: list[str] | None = None,
        linked_doc_revision_ids: list[str] | None = None,
        expires_in_hours: int | None = 48,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a new approval request.

        approval_type: 'plan_review' | 'risky_action' | 'fm_escalation' | 'budget' | 'manual'
        payload: {'title': str, 'summary': str, 'recommendedAction': str, 'risks': list[str]}
        linked_task_refs: task_id strings whose execution is gated by this approval.
        linked_doc_revision_ids: revision_ids of docs this approval references.
        expires_in_hours: TTL in hours (default 48). Pass null for no expiry.

        Enforces single-pending-per-task: if a task already has a pending approval,
        that approval is auto-cancelled with reason='superseded'.

        ``idempotency_key`` (PCA-948): retry-safe key (process-memory cache).
        Repeat call with the same key returns the original approval and
        sets ``idempotent_replay: True``.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.mcp.tools import _idempotency
        from cod_doc.services import activity_service, approval_service

        cached = _idempotency.check("approval_request", idempotency_key)
        if cached is not None:
            return dict(cached, idempotent_replay=True)

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            approval = approval_service.request(
                session,
                project_id,
                approval_type=approval_type,
                requested_by=requested_by,
                payload=payload,
                linked_task_refs=linked_task_refs,
                linked_doc_revision_ids=linked_doc_revision_ids,
                expires_in_hours=expires_in_hours,
            )
            activity_service.emit(
                session,
                project_id,
                "approval.requested",
                actor_kind=actor_kind_for_author(requested_by),
                actor_id=requested_by,
                scope_kind="approval",
                scope_id=approval.approval_id,
                payload={
                    "approval_type": approval_type,
                    "linked_tasks": linked_task_refs or [],
                },
                summary=f"Approval requested: {approval_type} by {requested_by}",
            )

        out = {
            "approval_id": approval.approval_id,
            "status": approval.status,
            "approval_type": approval.approval_type,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            "linked_task_refs": approval.linked_task_refs,
        }
        _idempotency.store("approval_request", idempotency_key, out)
        return out

    @mcp.tool(name="approval_list")
    def approval_list(
        project: str,
        status: str | None = None,
        approval_type: str | None = None,
        since: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List approvals. Default: all, newest first.

        status: 'pending' | 'approved' | 'denied' | 'cancelled' | 'expired'
        since: ISO-8601 datetime string (UTC).
        """
        from datetime import datetime

        from cod_doc.infra.db import transactional
        from cod_doc.services import approval_service

        since_dt = datetime.fromisoformat(since) if since else None
        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            return approval_service.list_approvals(
                session,
                project_id,
                status=status,
                approval_type=approval_type,
                since=since_dt,
                limit=limit,
                offset=offset,
            )

    @mcp.tool(name="approval_get")
    def approval_get(project: str, approval_id: str) -> dict[str, Any] | None:
        """Get a single approval by its ID. Returns null if not found."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import approval_service

        sf, _ = session_factory(project)
        with transactional(sf) as session:
            project_id = require_project_id(session, project)
            approval = approval_service.get(session, project_id, approval_id)
        if approval is None:
            return None
        return {
            "approval_id": approval.approval_id,
            "approval_type": approval.approval_type,
            "status": approval.status,
            "requested_by": approval.requested_by,
            "requested_at": approval.requested_at.isoformat() if approval.requested_at else None,
            "resolved_by": approval.resolved_by,
            "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
            "payload": approval.payload,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            "decision_comment": approval.decision_comment,
            "run_id": approval.run_id,
            "linked_task_refs": approval.linked_task_refs,
            "linked_doc_revision_ids": approval.linked_doc_revision_ids,
        }

    @mcp.tool(name="approval_resolve")
    def approval_resolve(
        project: str,
        approval_id: str,
        decision: str,
        resolved_by: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an approval (approve or deny).

        decision: 'approve' | 'deny'
        resolved_by: operator identifier.

        Returns: {'approval': {...}, 'wake_hint': {...} | null}

        ``wake_hint`` — if non-null, pass its fields to ``run_agent_once`` to
        resume the requesting agent: wake_reason='approval_resolved',
        task_id=wake_hint.task_id.
        """
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, approval_service

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                result = approval_service.resolve(
                    session,
                    project_id,
                    approval_id,
                    decision=decision,
                    resolved_by=resolved_by,
                    comment=comment,
                )
                activity_service.emit(
                    session,
                    project_id,
                    "approval.resolved",
                    actor_kind=actor_kind_for_author(resolved_by),
                    actor_id=resolved_by,
                    scope_kind="approval",
                    scope_id=approval_id,
                    payload={"decision": decision, "comment": comment},
                    summary=f"Approval {decision}d by {resolved_by}",
                )
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
        return result

    @mcp.tool(name="approval_cancel")
    def approval_cancel(
        project: str,
        approval_id: str,
        reason: str,
        cancelled_by: str = "mcp",
    ) -> dict[str, Any]:
        """Cancel a pending approval."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service, approval_service

        sf, _ = session_factory(project)
        try:
            with transactional(sf) as session:
                project_id = require_project_id(session, project)
                approval = approval_service.cancel(
                    session, project_id, approval_id, reason=reason, cancelled_by=cancelled_by
                )
                activity_service.emit(
                    session,
                    project_id,
                    "approval.cancelled",
                    actor_kind=actor_kind_for_author(cancelled_by),
                    actor_id=cancelled_by,
                    scope_kind="approval",
                    scope_id=approval_id,
                    payload={"reason": reason},
                    summary=f"Approval cancelled: {reason}",
                )
        except LookupError as exc:
            raise ValueError(str(exc)) from exc
        return {
            "approval_id": approval.approval_id,
            "status": approval.status,
            "decision_comment": approval.decision_comment,
        }
