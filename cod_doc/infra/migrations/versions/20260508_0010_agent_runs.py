"""PCA-030: agent_runs table + run_id column on revision/audit_log.

Closes proposal 04 (Run-id audit trail) data layer. Each Orchestrator
heartbeat gets a row in `agent_runs`; downstream mutations (revisions,
audit-log entries) carry the same run_id so "what did the agent do on
that run" becomes a single SELECT.

Revision ID: 0010_agent_runs
Revises: 0009_task_normalized_title
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0010_agent_runs"
down_revision: str | None = "0009_task_normalized_title"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_run",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        # UUID7 for sortability — 26 chars (ULID-like) for compactness; either
        # value is sortable and uniquely identifies a run.
        sa.Column("run_id", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wake_reason", sa.String(32), nullable=True),
        sa.Column("triggering_task_id", sa.String(32), nullable=True),
        sa.Column("triggering_doc_ref", sa.String(255), nullable=True),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_tokens_out", sa.Integer(), nullable=False, server_default="0"),
        # status: running | done | failed | cancelled
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_agent_run_project_started", "agent_run", ["project_id", "started_at"])
    op.create_index("ix_agent_run_status", "agent_run", ["status"])
    op.create_index(
        "ix_agent_run_triggering_task", "agent_run", ["triggering_task_id"]
    )

    # Stamp run_id on mutating tables. NULL = human / external mutation.
    with op.batch_alter_table("revision") as batch:
        batch.add_column(sa.Column("run_id", sa.String(36), nullable=True))
    op.create_index("ix_revision_run_id", "revision", ["run_id"])

    with op.batch_alter_table("audit_log") as batch:
        batch.add_column(sa.Column("run_id", sa.String(36), nullable=True))
    op.create_index("ix_audit_log_run_id", "audit_log", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_run_id", table_name="audit_log")
    with op.batch_alter_table("audit_log") as batch:
        batch.drop_column("run_id")

    op.drop_index("ix_revision_run_id", table_name="revision")
    with op.batch_alter_table("revision") as batch:
        batch.drop_column("run_id")

    op.drop_index("ix_agent_run_triggering_task", table_name="agent_run")
    op.drop_index("ix_agent_run_status", table_name="agent_run")
    op.drop_index("ix_agent_run_project_started", table_name="agent_run")
    op.drop_table("agent_run")
