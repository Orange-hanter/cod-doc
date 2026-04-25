"""COD-003: user_story + story_acceptance + story_link + module (+ deps + code).

Implements DATA_MODEL.md §3.10-3.11.

Revision ID: 0003_stories
Revises: 0002_tasks
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_stories"
down_revision: str | None = "0002_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_story",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("story_id", sa.String(32), nullable=False, unique=True),
        sa.Column("persona", sa.String(128), nullable=False),
        sa.Column("narrative", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "story_acceptance",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "story_id",
            sa.Integer,
            sa.ForeignKey("user_story.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("criterion", sa.Text, nullable=False),
        sa.Column("met", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "story_link",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "story_id",
            sa.Integer,
            sa.ForeignKey("user_story.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("to_kind", sa.String(16), nullable=False),
        sa.Column("to_ref", sa.String(255), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
    )

    op.create_table(
        "module",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module_id", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "spec_doc_id",
            sa.Integer,
            sa.ForeignKey("document.row_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "plan_id",
            sa.Integer,
            sa.ForeignKey("plan.row_id", ondelete="SET NULL"),
        ),
    )

    op.create_table(
        "module_dependency",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "from_module",
            sa.Integer,
            sa.ForeignKey("module.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_module",
            sa.Integer,
            sa.ForeignKey("module.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text),
        sa.UniqueConstraint(
            "from_module", "to_module", name="uq_module_dependency_edge"
        ),
    )

    op.create_table(
        "module_code",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "module_id",
            sa.Integer,
            sa.ForeignKey("module.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("module_code")
    op.drop_table("module_dependency")
    op.drop_table("module")
    op.drop_table("story_link")
    op.drop_table("story_acceptance")
    op.drop_table("user_story")
