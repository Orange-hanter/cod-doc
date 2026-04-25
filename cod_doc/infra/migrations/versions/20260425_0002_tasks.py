"""COD-002: plan + plan_section + task + dependency + affected_file (+ views).

Implements DATA_MODEL.md §3.6-3.9 and §4.1, §4.2, §4.3.

Revision ID: 0002_tasks
Revises: 0001_core
Create Date: 2026-04-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_tasks"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SECTION_TOTALS_VIEW = """
CREATE VIEW section_totals AS
SELECT
  s.row_id AS section_id,
  COUNT(t.row_id) AS tasks_total,
  COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
  COALESCE(SUM(CASE WHEN t.status = 'in-progress' THEN 1 ELSE 0 END), 0) AS tasks_in_progress
FROM plan_section s
LEFT JOIN task t ON t.section_id = s.row_id
GROUP BY s.row_id
"""

PLAN_TOTALS_VIEW = """
CREATE VIEW plan_totals AS
SELECT
  p.row_id AS plan_id,
  COUNT(t.row_id) AS tasks_total,
  COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
  COALESCE(SUM(CASE WHEN t.status = 'in-progress' THEN 1 ELSE 0 END), 0) AS tasks_in_progress
FROM plan p
LEFT JOIN task t ON t.plan_id = p.row_id
GROUP BY p.row_id
"""

READY_TASKS_VIEW = """
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status = 'pending'
  AND NOT EXISTS (
    SELECT 1
    FROM dependency d
    JOIN task dep ON dep.row_id = d.to_task_id
    WHERE d.from_task_id = t.row_id
      AND d.kind = 'blocks'
      AND dep.status <> 'done'
  )
"""


def upgrade() -> None:
    op.create_table(
        "plan",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(128), nullable=False, unique=True),
        sa.Column("principle", sa.String(32)),
        sa.Column("module_id", sa.String(64)),
        sa.Column(
            "parent_doc_id",
            sa.Integer,
            sa.ForeignKey("document.row_id", ondelete="SET NULL"),
        ),
        sa.Column(
            "completed_log_id",
            sa.Integer,
            sa.ForeignKey("document.row_id", ondelete="SET NULL"),
        ),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "plan_section",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "plan_id",
            sa.Integer,
            sa.ForeignKey("plan.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("letter", sa.String(4), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column(
            "doc_id",
            sa.Integer,
            sa.ForeignKey("document.row_id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("plan_id", "letter", name="uq_plan_section_plan_letter"),
    )

    op.create_table(
        "task",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "plan_id",
            sa.Integer,
            sa.ForeignKey("plan.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.Integer,
            sa.ForeignKey("plan_section.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("acceptance", sa.Text),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_commit", sa.String(64)),
    )
    op.create_index("ix_task_status", "task", ["status", "priority"])
    op.create_index("ix_task_plan", "task", ["plan_id", "section_id"])

    op.create_table(
        "dependency",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "from_task_id",
            sa.Integer,
            sa.ForeignKey("task.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_task_id",
            sa.Integer,
            sa.ForeignKey("task.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False, server_default="blocks"),
        sa.Column("note", sa.Text),
        sa.UniqueConstraint(
            "from_task_id", "to_task_id", "kind", name="uq_dependency_edge"
        ),
    )

    op.create_table(
        "affected_file",
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "task_id",
            sa.Integer,
            sa.ForeignKey("task.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="source"),
        sa.UniqueConstraint("task_id", "path", name="uq_affected_file_task_path"),
    )
    op.create_index("ix_affected_path", "affected_file", ["path"])

    op.execute(SECTION_TOTALS_VIEW)
    op.execute(PLAN_TOTALS_VIEW)
    op.execute(READY_TASKS_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    op.execute("DROP VIEW IF EXISTS plan_totals")
    op.execute("DROP VIEW IF EXISTS section_totals")

    op.drop_index("ix_affected_path", table_name="affected_file")
    op.drop_table("affected_file")
    op.drop_table("dependency")
    op.drop_index("ix_task_plan", table_name="task")
    op.drop_index("ix_task_status", table_name="task")
    op.drop_table("task")
    op.drop_table("plan_section")
    op.drop_table("plan")
