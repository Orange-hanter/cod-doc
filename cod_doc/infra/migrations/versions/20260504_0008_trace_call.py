"""COD-063: trace_call table — per-task LLM-call ledger.

One row per LLM round-trip (chat completion or embedding call). Links to
the originating task (when the call is task-bound), records token usage,
duration and any tool calls so the task-detail "Trace" tab can show exact
cost + tool-use history.

Tasks ↔ traces is a 1-to-many via task_id (nullable: agent calls without
a task — e.g. master-md generation — still get logged with task_id NULL).

Revision ID: 0008_trace_call
Revises: 0007_task_blocked_reason
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0008_trace_call"
down_revision: str | None = "0007_task_blocked_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_call",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("task.row_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="chat"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Text(), nullable=True),  # JSON-encoded list
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index("ix_trace_call_task_id_ts", "trace_call", ["task_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_trace_call_task_id_ts", table_name="trace_call")
    op.drop_table("trace_call")
