"""TSC-001: scenario / scenario_step / scenario_link.

The authoring half of [RFC 24 §9] — the "normalized scenario index" §12 defers.
Intentions only: coverage verdicts (`covered | partial | missing |
unverifiable`) are evidence derived by the structure producer and land in a
separate `scenario_assessment` table in STR-002, joined on `scenario.row_id`.

No `server_default` is used: every write goes through `scenario_service`, and
quoted server-side literals have already broken PostgreSQL `CREATE TABLE` in
earlier migrations (STO-019).

Revision ID: 0031_scenarios
Revises: 0030_document_body_pushdown
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0031_scenarios"
down_revision: str | None = "0030_document_body_pushdown"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scenario",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scenario_id", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("group_key", sa.String(64), nullable=False),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("document.row_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("doc_key", sa.String(255), nullable=True),
        sa.Column("section_anchor", sa.String(255), nullable=True),
        sa.Column("doc_content_hash", sa.String(64), nullable=True),
        sa.Column(
            "module_id",
            sa.Integer(),
            sa.ForeignKey("module.row_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("subject_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provenance", sa.String(16), nullable=False),
        sa.Column("preconditions", sa.Text(), nullable=False),
        sa.Column("expected", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "scenario_id", name="uq_scenario_project_sid"),
    )
    op.create_index(
        "ix_scenario_project_group", "scenario", ["project_id", "group_key", "position"]
    )
    op.create_index("ix_scenario_document_id", "scenario", ["document_id"])
    op.create_index("ix_scenario_project_kind", "scenario", ["project_id", "kind"])

    op.create_table(
        "scenario_step",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_row_id",
            sa.Integer(),
            sa.ForeignKey("scenario.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("scenario_row_id", "position", name="uq_scenario_step_position"),
    )
    op.create_index("ix_scenario_step_scenario", "scenario_step", ["scenario_row_id"])

    op.create_table(
        "scenario_link",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "scenario_row_id",
            sa.Integer(),
            sa.ForeignKey("scenario.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("to_kind", sa.String(16), nullable=False),
        sa.Column("to_ref", sa.String(255), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "scenario_row_id",
            "to_kind",
            "to_ref",
            "relation",
            name="uq_scenario_link_edge",
        ),
    )
    op.create_index("ix_scenario_link_scenario", "scenario_link", ["scenario_row_id"])


def downgrade() -> None:
    op.drop_index("ix_scenario_link_scenario", table_name="scenario_link")
    op.drop_table("scenario_link")

    op.drop_index("ix_scenario_step_scenario", table_name="scenario_step")
    op.drop_table("scenario_step")

    op.drop_index("ix_scenario_project_kind", table_name="scenario")
    op.drop_index("ix_scenario_document_id", table_name="scenario")
    op.drop_index("ix_scenario_project_group", table_name="scenario")
    op.drop_table("scenario")
