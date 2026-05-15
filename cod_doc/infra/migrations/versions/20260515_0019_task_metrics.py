"""OBI-001: task_metrics table — per-task completion stats.

Captured on ``task_service.complete`` to provide later observability
(`/p/{slug}/metrics` sparklines + percentiles in OBI-002).

Stored fields:
- ``duration_hours``        — wall-clock created → completed_at.
- ``in_progress_hours``     — time spent in 'in_progress' (vs blocked/review).
                              Best-effort from revision timeline; null if data missing.
- ``revision_count``        — count of TASK revisions during the task's life.
- ``blocked_hours``         — total time in 'blocked' status (nullable).
- ``commit_count``          — count of linked commits (filled by OBI-010 later).
- ``priority`` + ``type``   — snapshot for slice-and-dice reporting.

One row per completed task; unique (task_id) enforces idempotency on replay.

Revision ID: 0019_task_metrics
Revises: 0018_adr_tables
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_task_metrics"
down_revision = "0018_adr_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_metrics",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.Integer,
            sa.ForeignKey("task.row_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_hours", sa.Float, nullable=False),
        sa.Column("in_progress_hours", sa.Float, nullable=True),
        sa.Column("blocked_hours", sa.Float, nullable=True),
        sa.Column("revision_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("commit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
    )
    op.create_index("ix_task_metrics_project", "task_metrics", ["project_id"])
    op.create_index(
        "ix_task_metrics_completed", "task_metrics", ["project_id", "completed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_metrics_completed", table_name="task_metrics")
    op.drop_index("ix_task_metrics_project", table_name="task_metrics")
    op.drop_table("task_metrics")
