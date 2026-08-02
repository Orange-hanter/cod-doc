"""Approval entities — first-class structured decisions (PCA-120, proposal 12).

Approvals gate risky or policy-required operations. On creation, linked
tasks move to `in_review`; on resolve, the requesting agent is woken via
WakeContext (proposal 03). Each approval carries a full audit trail:
who requested, when, what was decided, by whom, with what comment.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class ApprovalModel(Base):
    """A pending or resolved decision gate.

    Types: 'plan_review' | 'risky_action' | 'fm_escalation' | 'budget' | 'manual'
    Status: 'pending' | 'approved' | 'denied' | 'cancelled' | 'expired'

    ``run_id`` links to the agent run that requested the approval (NULL for
    human-initiated approvals). ``payload_json`` carries type-specific
    context: title, summary, recommendedAction, risks.

    Linked tasks are stored in ``approval_task_link``. Linked doc revision
    IDs (e.g. "approve plan@rev-abc") are stored in
    ``approval_doc_revision_link``.
    """

    __tablename__ = "approval"
    __table_args__ = (
        Index("ix_approval_project_status", "project_id", "status"),
        Index("ix_approval_project_ts", "project_id", "requested_at"),
        Index("ix_approval_run_id", "run_id"),
        Index("ix_approval_status", "status"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approval_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    approval_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    # run_id of the orchestrator run that created this approval (proposal 04).
    run_id: Mapped[str | None] = mapped_column(String(36))


class ApprovalTaskLinkModel(Base):
    """M:N link between an approval and the tasks it gates."""

    __tablename__ = "approval_task_link"
    __table_args__ = (
        Index("ix_approval_task_link_approval", "approval_id"),
        Index("ix_approval_task_link_task", "task_ref"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approval_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("approval.row_id", ondelete="CASCADE"), nullable=False
    )
    # Stores the human-readable task_id string (PCA-xxx, COD-xxx) for simpler
    # cross-referencing without a hard FK join. Validated at service layer.
    task_ref: Mapped[str] = mapped_column(String(32), nullable=False)


class ApprovalDocRevisionLinkModel(Base):
    """Doc revision IDs referenced by an approval (e.g. 'approve plan@rev-abc')."""

    __tablename__ = "approval_doc_revision_link"
    __table_args__ = (Index("ix_approval_doc_rev_approval", "approval_id"),)

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approval_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("approval.row_id", ondelete="CASCADE"), nullable=False
    )
    revision_id: Mapped[str] = mapped_column(String(26), nullable=False)
