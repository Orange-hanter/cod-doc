"""link_suggestion table for semantic backfill (PCA-422).

Revision ID: 20260508_0015
Revises: 20260508_0014
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_link_suggestions"
down_revision = "0014_task_checkout_and_routine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "link_suggestion",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("from_section_id", sa.Integer, nullable=False),
        sa.Column("to_doc_key", sa.String(512), nullable=False),
        sa.Column("to_section_id", sa.Integer, nullable=True),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("evidence", sa.Text, nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lsugg_from_section", "link_suggestion", ["from_section_id"])
    op.create_index("ix_lsugg_state", "link_suggestion", ["state"])
    op.create_index(
        "uq_lsugg_triple",
        "link_suggestion",
        ["from_section_id", "to_doc_key", "to_section_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_lsugg_triple", table_name="link_suggestion")
    op.drop_index("ix_lsugg_state", table_name="link_suggestion")
    op.drop_index("ix_lsugg_from_section", table_name="link_suggestion")
    op.drop_table("link_suggestion")
