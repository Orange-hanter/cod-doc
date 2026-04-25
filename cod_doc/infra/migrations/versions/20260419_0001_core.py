"""COD-001: core tables (project, document, section, link).

Implements DATA_MODEL.md §3.1-3.4 + §3.3 sensitivity column.

Revision ID: 0001_core
Revises:
Create Date: 2026-04-19
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("root_path", sa.Text, nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_json", sa.JSON, nullable=False),
    )

    op.create_table(
        "document",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_key", sa.String(255), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_of_truth", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "sensitivity",
            sa.String(16),
            nullable=False,
            server_default="internal",
        ),
        sa.Column("owner", sa.String(64)),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("preamble", sa.Text, nullable=False, server_default=""),
        sa.Column("frontmatter_json", sa.JSON, nullable=False),
        sa.Column("projection_hash", sa.String(64)),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reviewed", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "doc_key", name="uq_document_project_key"),
    )
    op.create_index("ix_document_type", "document", ["type", "status"])
    op.create_index("ix_document_sensitivity", "document", ["sensitivity"])

    op.create_table(
        "section",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("document.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("anchor", sa.String(255), nullable=False),
        sa.Column("heading", sa.Text, nullable=False),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("document_id", "anchor", name="uq_section_document_anchor"),
    )
    op.create_index("ix_section_position", "section", ["document_id", "position"])

    op.create_table(
        "link",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_section_id",
            sa.Integer,
            sa.ForeignKey("section.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw", sa.Text, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("to_doc_key", sa.String(255)),
        sa.Column("to_task_id", sa.String(32)),
        sa.Column("to_story_id", sa.String(32)),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_checked", sa.DateTime(timezone=True)),
        sa.Column("broken_reason", sa.Text),
    )
    op.create_index("ix_link_target_doc", "link", ["to_doc_key"])
    op.create_index("ix_link_target_task", "link", ["to_task_id"])
    op.create_index("ix_link_unresolved", "link", ["resolved"])


def downgrade() -> None:
    op.drop_index("ix_link_unresolved", table_name="link")
    op.drop_index("ix_link_target_task", table_name="link")
    op.drop_index("ix_link_target_doc", table_name="link")
    op.drop_table("link")
    op.drop_index("ix_section_position", table_name="section")
    op.drop_table("section")
    op.drop_index("ix_document_sensitivity", table_name="document")
    op.drop_index("ix_document_type", table_name="document")
    op.drop_table("document")
    op.drop_table("project")
