"""SYM-005C: findings tables + FTS scope.

Creates the three findings-related tables verbatim from RFC 22 §3.2
(`finding`, `finding_source_run`, `external_ref`) and adds `finding` to the
FTS5 search surface.

Revision ID: 0028_findings
Revises: 0027_shared_hub
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0028_findings"
down_revision: str | None = "0027_shared_hub"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "finding",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_uid", sa.String(36), nullable=False, unique=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="'open'"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("promoted_task_id", sa.String(32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="'{}'"),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.UniqueConstraint(
            "project_id", "source", "fingerprint", name="uq_finding_project_source_fp"
        ),
    )
    op.create_index("ix_finding_project_id", "finding", ["project_id"])

    op.create_table(
        "finding_source_run",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "finding_id",
            sa.Integer(),
            sa.ForeignKey("finding.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_run_id", sa.String(128), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity_at_run", sa.String(16), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
    )
    op.create_index("ix_finding_source_run_finding_id", "finding_source_run", ["finding_id"])

    op.create_table(
        "external_ref",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_kind", sa.String(32), nullable=False),
        sa.Column("entity_row_id", sa.Integer(), nullable=False),
        sa.Column("system", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "project_id", "system", "external_id", name="uq_external_ref_project_system_id"
        ),
    )
    op.create_index("ix_external_ref_project_id", "external_ref", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_external_ref_project_id", table_name="external_ref")
    op.drop_table("external_ref")
    op.drop_index("ix_finding_source_run_finding_id", table_name="finding_source_run")
    op.drop_table("finding_source_run")
    op.drop_index("ix_finding_project_id", table_name="finding")
    op.drop_table("finding")
