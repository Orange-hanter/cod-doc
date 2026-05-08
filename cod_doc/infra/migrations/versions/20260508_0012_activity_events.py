"""PCA-110: activity_events table (unified audit timeline).

Closes proposal 09 (Activity & Events log). Append-only stream of all
mutating actions — task status changes, doc updates, agent runs, approvals.
Indexed for efficient per-scope and per-run queries.

Revision ID: 0012_activity_events
Revises: 0011_task_documents
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0012_activity_events"
down_revision: str | None = "0011_task_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_event",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        # UUID7: time-sortable, globally unique event ID.
        sa.Column("id", sa.String(36), nullable=False, unique=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        # actor_kind: 'orchestrator' | 'human' | 'routine' | 'system'
        sa.Column("actor_kind", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        # run_id from agent_run table; NULL for direct human/CLI actions.
        sa.Column("run_id", sa.String(36), nullable=True),
        # Canonical event kind (see proposal 09).
        sa.Column("kind", sa.String(64), nullable=False),
        # Scope: 'task' | 'doc' | 'task_doc' | 'story' | 'project' | 'approval' | 'run'
        sa.Column("scope_kind", sa.String(32), nullable=True),
        # Human-readable scope ID (task_id string, doc_key, story_id, …).
        sa.Column("scope_id", sa.String(255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="'{}'"),
        sa.Column("summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_activity_event_ts", "activity_event", ["ts"])
    op.create_index("ix_activity_event_scope", "activity_event", ["scope_kind", "scope_id", "ts"])
    op.create_index("ix_activity_event_run", "activity_event", ["run_id"])
    op.create_index("ix_activity_event_project_ts", "activity_event", ["project_id", "ts"])
    op.create_index("ix_activity_event_kind", "activity_event", ["kind", "ts"])


def downgrade() -> None:
    op.drop_index("ix_activity_event_kind", table_name="activity_event")
    op.drop_index("ix_activity_event_project_ts", table_name="activity_event")
    op.drop_index("ix_activity_event_run", table_name="activity_event")
    op.drop_index("ix_activity_event_scope", table_name="activity_event")
    op.drop_index("ix_activity_event_ts", table_name="activity_event")
    op.drop_table("activity_event")
