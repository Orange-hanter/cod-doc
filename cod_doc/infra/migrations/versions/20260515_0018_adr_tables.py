"""ADR-001: Architecture Decision Records — adr + diagrams + supersedes + task links.

First-class storage for project ADRs:
- ``adr``                — the core record (id, status, context, decision, alts, consequences)
- ``adr_diagram``        — optional Mermaid diagrams attached to an ADR
- ``adr_supersedes``     — DAG of "ADR-N replaces ADR-M" relationships
- ``adr_task``           — links from ADRs to tasks (implements / invalidates / discovers)

ADR status taxonomy: proposed | accepted | superseded | deprecated | rejected.
Canonical id format: ``ADR-NNN`` (e.g. ADR-001), unique within a project.

Revision ID: 0018_adr_tables
Revises: 0017_doc_comments
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_adr_tables"
down_revision = "0017_doc_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adr",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("adr_id", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("decided_at", sa.Date, nullable=True),
        sa.Column("context", sa.Text, nullable=True),
        sa.Column("decision", sa.Text, nullable=True),
        sa.Column("alternatives", sa.Text, nullable=True),
        sa.Column("consequences", sa.Text, nullable=True),
        sa.Column("author", sa.String(128), nullable=False, server_default="human"),
        sa.Column(
            "created", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "last_updated", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint("project_id", "adr_id", name="uq_adr_project_id"),
        sa.CheckConstraint(
            "status IN ('proposed','accepted','superseded','deprecated','rejected')",
            name="ck_adr_status",
        ),
    )
    op.create_index("ix_adr_project_status", "adr", ["project_id", "status"])

    op.create_table(
        "adr_diagram",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "adr_id",
            sa.Integer,
            sa.ForeignKey("adr.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("title", sa.String(128), nullable=True),
        sa.Column("mermaid", sa.Text, nullable=False),
        sa.UniqueConstraint("adr_id", "position", name="uq_adr_diagram_position"),
    )
    op.create_index("ix_adr_diagram_adr", "adr_diagram", ["adr_id"])

    op.create_table(
        "adr_supersedes",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "superseding_id",
            sa.Integer,
            sa.ForeignKey("adr.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "superseded_id",
            sa.Integer,
            sa.ForeignKey("adr.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint(
            "superseding_id", "superseded_id", name="uq_adr_supersedes_edge",
        ),
        sa.CheckConstraint(
            "superseding_id <> superseded_id",
            name="ck_adr_supersedes_no_self_loop",
        ),
    )
    op.create_index("ix_adr_supersedes_new", "adr_supersedes", ["superseding_id"])
    op.create_index("ix_adr_supersedes_old", "adr_supersedes", ["superseded_id"])

    op.create_table(
        "adr_task",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "adr_row_id",
            sa.Integer,
            sa.ForeignKey("adr.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(32), nullable=False),
        sa.Column(
            "relation", sa.String(16),
            nullable=False, server_default="implements",
        ),
        sa.UniqueConstraint(
            "adr_row_id", "task_id", "relation",
            name="uq_adr_task_link",
        ),
        sa.CheckConstraint(
            "relation IN ('implements','invalidates','discovers','relates')",
            name="ck_adr_task_relation",
        ),
    )
    op.create_index("ix_adr_task_task", "adr_task", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_adr_task_task", table_name="adr_task")
    op.drop_table("adr_task")
    op.drop_index("ix_adr_supersedes_old", table_name="adr_supersedes")
    op.drop_index("ix_adr_supersedes_new", table_name="adr_supersedes")
    op.drop_table("adr_supersedes")
    op.drop_index("ix_adr_diagram_adr", table_name="adr_diagram")
    op.drop_table("adr_diagram")
    op.drop_index("ix_adr_project_status", table_name="adr")
    op.drop_table("adr")
