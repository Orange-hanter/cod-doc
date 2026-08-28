"""SYM-005B: shared-hub preparation — composite UNIQUE on task(project_id, task_id).

Replaces the historical ``UNIQUE(task_id)`` with ``UNIQUE(project_id, task_id)``.
This is the schema precondition for running several cod-doc projects out of a
single shared database: two different projects may now use the same task ID,
but a project cannot contain duplicate task IDs.

The migration rebuilds the ``task`` table (SQLite cannot DROP/ALTER
constraints). Views that reference ``task`` are dropped around the rebuild and
restored verbatim. Foreign-key enforcement is disabled for the duration of the
rebuild so that tables referencing ``task`` (``dependency``, ``affected_file``,
``task_document``, ``task_metrics``, ``trace_call``, ``task_tag``) survive the
temporary drop; the constraint definitions themselves still name ``task`` and
validate once enforcement is re-enabled.

Revision ID: 0027_shared_hub
Revises: 0026_document_type_recoercion
Create Date: 2026-08-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_shared_hub"
down_revision = "0026_document_type_recoercion"
branch_labels = None
depends_on = None


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


def _drop_task_views() -> None:
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    op.execute("DROP VIEW IF EXISTS plan_totals")
    op.execute("DROP VIEW IF EXISTS section_totals")


def _create_task_views() -> None:
    op.execute(SECTION_TOTALS_VIEW)
    op.execute(PLAN_TOTALS_VIEW)
    op.execute(READY_TASKS_VIEW)


def _create_task_table(table_name: str, *, task_id_unique: bool) -> None:
    args: list[sa.Constraint] = []
    if task_id_unique:
        args.append(sa.UniqueConstraint("task_id", name="uq_task_task_id"))
    else:
        args.append(sa.UniqueConstraint("project_id", "task_id", name="uq_task_project_task_id"))

    op.create_table(
        table_name,
        sa.Column("row_id", sa.Integer, primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(32), nullable=False),
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
        sa.Column("blocked_reason", sa.Text),
        sa.Column("normalized_title", sa.Text),
        sa.Column("checked_out_by", sa.String(128)),
        sa.Column("checked_out_at", sa.DateTime(timezone=True)),
        sa.Column("expected_status_at_checkout", sa.String(16)),
        *args,
    )
    op.create_index("ix_task_status", table_name, ["status", "priority"])
    op.create_index("ix_task_plan", table_name, ["plan_id", "section_id"])
    op.create_index("ix_task_project_normtitle", table_name, ["project_id", "normalized_title"])
    op.create_index("ix_task_checked_out_by", table_name, ["checked_out_by"])


def _copy_tasks(src: str, dst: str) -> None:
    op.execute(f"INSERT INTO {dst} SELECT * FROM {src}")


def _drop_task_indexes(table_name: str) -> None:
    # Index names are global in SQLite; the old table still holds these names
    # while we build task_new, so drop them first.
    op.drop_index("ix_task_status", table_name=table_name)
    op.drop_index("ix_task_plan", table_name=table_name)
    op.drop_index("ix_task_project_normtitle", table_name=table_name)
    op.drop_index("ix_task_checked_out_by", table_name=table_name)


def _rebuild(task_id_unique: bool) -> None:
    """Rebuild ``task`` with the requested uniqueness rule on ``task_id``."""
    old = "task"
    new = "task_new"

    # Foreign-key enforcement must be off while we drop the table that other
    # tables reference. SQLite allows toggling the pragma inside the migration
    # transaction; references in sqlite_master still name ``task`` and validate
    # once enforcement is re-enabled after the rename.
    op.execute("PRAGMA foreign_keys=OFF")

    _drop_task_views()
    _drop_task_indexes(old)
    _create_task_table(new, task_id_unique=task_id_unique)
    _copy_tasks(old, new)
    op.drop_table(old)
    op.execute(f"ALTER TABLE {new} RENAME TO {old}")
    _create_task_views()

    op.execute("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    """Replace UNIQUE(task_id) with UNIQUE(project_id, task_id)."""
    _rebuild(task_id_unique=False)


def downgrade() -> None:
    """Restore UNIQUE(task_id).

    This is only reversible while ``task_id`` values are globally unique. If
    two projects share the same ``task_id`` after the upgrade, the downgrade
    fails with a clear error so data is not silently mutated.
    """
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT task_id, COUNT(DISTINCT project_id) AS projects "
            "FROM task GROUP BY task_id HAVING projects > 1"
        )
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            f"Cannot downgrade 0027_shared_hub: {len(duplicates)} task_id(s) are "
            "shared across multiple projects. Remove the duplicates first."
        )

    _rebuild(task_id_unique=True)
