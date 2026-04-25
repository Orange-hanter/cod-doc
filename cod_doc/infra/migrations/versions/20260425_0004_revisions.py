"""COD-004: revision (§3.5) + audit_log (§3.13).

Revision: append-only history with ULID `revision_id`, indexed for `cod-doc log`.
AuditLog: every write-path call from cli/mcp/rest/tui/agent.

Revision ID: 0004_revisions
Revises: 0003_stories
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_revisions"
down_revision: str | None = "0003_stories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revision",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column("revision_id", sa.String(26), nullable=False, unique=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_kind", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Integer, nullable=False),
        sa.Column("parent_revision_id", sa.String(26)),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("diff", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("commit_sha", sa.String(64)),
    )
    op.create_index("ix_revision_entity", "revision", ["entity_kind", "entity_id", "at"])
    op.create_index("ix_revision_parent", "revision", ["parent_revision_id"])

    op.create_table(
        "audit_log",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON, nullable=False),
        sa.Column("result", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_action", "audit_log", ["action", "at"])
    op.create_index("ix_audit_actor", "audit_log", ["actor", "at"])


def downgrade() -> None:
    op.drop_index("ix_audit_actor", table_name="audit_log")
    op.drop_index("ix_audit_action", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_revision_parent", table_name="revision")
    op.drop_index("ix_revision_entity", table_name="revision")
    op.drop_table("revision")
