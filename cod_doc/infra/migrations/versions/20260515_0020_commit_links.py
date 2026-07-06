"""OBI-010: commit_link table — git commits referencing task IDs.

Parser scans commit messages for task_id-shaped tokens (e.g. ``COD-042``,
``ADR-003``, ``OBI-010``). Each match becomes a ``commit_link`` row tying
the SHA to one task. Many-to-many: one commit may touch multiple tasks;
one task accrues commits across its lifetime.

Revision ID: 0020_commit_links
Revises: 0019_task_metrics
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_commit_links"
down_revision = "0019_task_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commit_link",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(32), nullable=False),
        sa.Column("sha", sa.String(64), nullable=False),
        sa.Column("short_sha", sa.String(12), nullable=False),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column("author", sa.String(128), nullable=False, server_default=""),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "project_id",
            "task_id",
            "sha",
            name="uq_commit_link_edge",
        ),
    )
    op.create_index("ix_commit_link_task", "commit_link", ["project_id", "task_id"])
    op.create_index("ix_commit_link_sha", "commit_link", ["project_id", "sha"])


def downgrade() -> None:
    op.drop_index("ix_commit_link_sha", table_name="commit_link")
    op.drop_index("ix_commit_link_task", table_name="commit_link")
    op.drop_table("commit_link")
