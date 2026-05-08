"""PCA-100: task_document table (task-bound docs with revisions).

Closes proposal 05 (Issue documents). Introduces per-task structured
documents keyed by a short string ('plan', 'design', 'verification',
'acceptance', …). Revision history re-uses the existing ``revision``
table with entity_kind='task_doc'.

Revision ID: 0011_task_documents
Revises: 0010_agent_runs
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0011_task_documents"
down_revision: str | None = "0010_agent_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_document",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer(),
            sa.ForeignKey("task.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("format", sa.String(16), nullable=False, server_default="markdown"),
        sa.Column("current_revision_id", sa.String(26), nullable=True),
        sa.Column(
            "created",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint("task_id", "key", name="uq_task_document_task_key"),
    )
    op.create_index("ix_task_document_task_id", "task_document", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_document_task_id", table_name="task_document")
    op.drop_table("task_document")
