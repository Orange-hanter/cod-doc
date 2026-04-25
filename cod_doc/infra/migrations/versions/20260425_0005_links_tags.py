"""COD-005: tag + junction tables (§3.12) and link-index alignment with spec.

- Adds tag, document_tag, task_tag, story_tag (DATA_MODEL §3.12).
- Replaces full ix_link_unresolved with the partial ix_link_broken
  (`WHERE resolved = 0`) defined in DATA_MODEL §3.4. Partial index keeps the
  hot read path (`broken-links report`) cheap as the resolved-link count grows.

Revision ID: 0005_links_tags
Revises: 0004_revisions
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_links_tags"
down_revision: str | None = "0004_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tag",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_tag_project_name"),
    )

    op.create_table(
        "document_tag",
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("document.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer,
            sa.ForeignKey("tag.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "task_tag",
        sa.Column(
            "task_id",
            sa.Integer,
            sa.ForeignKey("task.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer,
            sa.ForeignKey("tag.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "story_tag",
        sa.Column(
            "story_id",
            sa.Integer,
            sa.ForeignKey("user_story.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer,
            sa.ForeignKey("tag.row_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # Align link unresolved-index with DATA_MODEL §3.4 (partial WHERE resolved = 0).
    op.drop_index("ix_link_unresolved", table_name="link")
    op.create_index(
        "ix_link_broken",
        "link",
        ["resolved"],
        sqlite_where=sa.text("resolved = 0"),
        postgresql_where=sa.text("resolved = 0"),
    )


def downgrade() -> None:
    op.drop_index("ix_link_broken", table_name="link")
    op.create_index("ix_link_unresolved", "link", ["resolved"])

    op.drop_table("story_tag")
    op.drop_table("task_tag")
    op.drop_table("document_tag")
    op.drop_table("tag")
