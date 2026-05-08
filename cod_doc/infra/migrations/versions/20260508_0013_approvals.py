"""PCA-120: approval table + indexes (first-class approvals).

Closes proposal 12 (First-class Approvals). Structured decision gates
that can pause tasks and wake agents on resolution. Linked tasks and
doc-revision references are stored in join tables.

Revision ID: 0013_approvals
Revises: 0012_activity_events
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0013_approvals"
down_revision: str | None = "0012_activity_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        # UUID7: time-sortable, globally unique approval ID.
        sa.Column("approval_id", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # approval_type: 'plan_review' | 'risky_action' | 'fm_escalation' | 'budget' | 'manual'
        sa.Column("approval_type", sa.String(32), nullable=False),
        # status: 'pending' | 'approved' | 'denied' | 'cancelled' | 'expired'
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        # JSON blob: title, summary, recommendedAction, risks, links.
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default="'{}'"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        # run_id of the agent run that created this approval (proposal 04).
        sa.Column("run_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_approval_project_status", "approval", ["project_id", "status"])
    op.create_index("ix_approval_project_ts", "approval", ["project_id", "requested_at"])
    op.create_index("ix_approval_run_id", "approval", ["run_id"])
    op.create_index("ix_approval_status", "approval", ["status"])

    op.create_table(
        "approval_task_link",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "approval_id",
            sa.Integer(),
            sa.ForeignKey("approval.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        # task_ref: human-readable task_id string (PCA-xxx, COD-xxx).
        sa.Column("task_ref", sa.String(32), nullable=False),
    )
    op.create_index("ix_approval_task_link_approval", "approval_task_link", ["approval_id"])
    op.create_index("ix_approval_task_link_task", "approval_task_link", ["task_ref"])

    op.create_table(
        "approval_doc_revision_link",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "approval_id",
            sa.Integer(),
            sa.ForeignKey("approval.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_id", sa.String(26), nullable=False),
    )
    op.create_index(
        "ix_approval_doc_rev_approval", "approval_doc_revision_link", ["approval_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_approval_doc_rev_approval", table_name="approval_doc_revision_link")
    op.drop_table("approval_doc_revision_link")

    op.drop_index("ix_approval_task_link_task", table_name="approval_task_link")
    op.drop_index("ix_approval_task_link_approval", table_name="approval_task_link")
    op.drop_table("approval_task_link")

    op.drop_index("ix_approval_status", table_name="approval")
    op.drop_index("ix_approval_run_id", table_name="approval")
    op.drop_index("ix_approval_project_ts", table_name="approval")
    op.drop_index("ix_approval_project_status", table_name="approval")
    op.drop_table("approval")
