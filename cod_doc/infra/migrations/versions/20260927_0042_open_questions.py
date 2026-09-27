"""OQM-001: open_question / open_question_option / open_question_link.

Открытые вопросы — отдельная сущность БД (decisions-and-questions §2), а не
документ ``type: open-question``. Таблицы новые, ``document`` не трогается,
поэтому ``batch_alter_table`` не нужен.

No ``server_default``: every write goes through ``question_service`` (the same
reasoning as 0032_scenarios, STO-019).

Revision ID: 0042_open_questions
Revises: 0041_ready_tasks_blocked_reason
Create Date: 2026-09-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0042_open_questions"
down_revision: str | None = "0041_ready_tasks_blocked_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "open_question",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_id", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(64), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("resolved_by_adr", sa.String(16), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_doc_key", sa.String(255), nullable=True),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "question_id", name="uq_open_question_project_qid"),
        sa.CheckConstraint(
            "status IN ('open','resolved','dropped')",
            name="ck_open_question_status",
        ),
    )
    op.create_index("ix_open_question_project_status", "open_question", ["project_id", "status"])

    op.create_table(
        "open_question_option",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "question_row_id",
            sa.Integer(),
            sa.ForeignKey("open_question.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("chosen", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("question_row_id", "position", name="uq_open_question_option_position"),
    )
    op.create_index("ix_open_question_option_question", "open_question_option", ["question_row_id"])

    op.create_table(
        "open_question_link",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "question_row_id",
            sa.Integer(),
            sa.ForeignKey("open_question.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("to_kind", sa.String(16), nullable=False),
        sa.Column("to_ref", sa.String(512), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=True),
        sa.Column("broken_reason", sa.Text(), nullable=True),
        sa.Column("last_checked", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "question_row_id",
            "to_kind",
            "to_ref",
            "relation",
            name="uq_open_question_link_edge",
        ),
    )
    op.create_index("ix_open_question_link_question", "open_question_link", ["question_row_id"])
    op.create_index("ix_open_question_link_target", "open_question_link", ["to_kind", "to_ref"])


def downgrade() -> None:
    op.drop_index("ix_open_question_link_target", table_name="open_question_link")
    op.drop_index("ix_open_question_link_question", table_name="open_question_link")
    op.drop_table("open_question_link")

    op.drop_index("ix_open_question_option_question", table_name="open_question_option")
    op.drop_table("open_question_option")

    op.drop_index("ix_open_question_project_status", table_name="open_question")
    op.drop_table("open_question")
