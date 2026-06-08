"""PCA-200 + PCA-210: task lock fields + routine/routine_run tables.

Two independent additive changes bundled in one migration to avoid
chain churn:

1. **Task lock fields** (PCA-200, proposal 06): adds
   ``checked_out_by``, ``checked_out_at``, ``expected_status_at_checkout``
   to the ``task`` table. NULL = no active checkout.

2. **Routine entity** (PCA-210, proposal 07): introduces ``routine`` and
   ``routine_run`` tables for cron-style health checks.

Revision ID: 0014_task_checkout_and_routine
Revises: 0013_approvals
Create Date: 2026-05-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0014_task_checkout_and_routine"
down_revision: str | None = "0013_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- Part 1: Task lock fields (PCA-200) ----
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("checked_out_by", sa.String(128), nullable=True))
        batch.add_column(sa.Column("checked_out_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("expected_status_at_checkout", sa.String(16), nullable=True))
    op.create_index("ix_task_checked_out_by", "task", ["checked_out_by"])

    # ---- Part 2: Routine + RoutineRun (PCA-210) ----
    op.create_table(
        "routine",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        # trigger: 'cron' | 'manual' | 'event'
        sa.Column("trigger", sa.String(16), nullable=False, server_default="cron"),
        # Cron expression (e.g. "0 9 * * MON"). NULL for manual/event triggers.
        sa.Column("cron", sa.String(64), nullable=True),
        # Catalog name of the check function (e.g. "stale_refs", "doc_drift").
        sa.Column("check_name", sa.String(64), nullable=False),
        sa.Column("check_args", sa.JSON(), nullable=False, server_default="'{}'"),
        # on_finding policy: 'create_task' | 'update_existing_task' | 'comment_only'
        sa.Column("on_finding", sa.String(32), nullable=False, server_default="comment_only"),
        # concurrency: 'skip' | 'queue' | 'parallel'
        sa.Column("concurrency", sa.String(8), nullable=False, server_default="skip"),
        # catch_up: 'skip' | 'run_latest' | 'run_all'
        sa.Column("catch_up", sa.String(16), nullable=False, server_default="run_latest"),
        sa.Column(
            "created",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_routine_project_name"),
    )
    op.create_index("ix_routine_enabled", "routine", ["enabled"])

    op.create_table(
        "routine_run",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "routine_id",
            sa.Integer(),
            sa.ForeignKey("routine.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        # status: 'running' | 'done' | 'failed' | 'skipped'
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("findings_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_task_id", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        # run_id from agent_run if the routine spawned an orchestrator run.
        sa.Column("run_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_routine_run_routine_started", "routine_run", ["routine_id", "started_at"])
    op.create_index("ix_routine_run_status", "routine_run", ["status"])


def downgrade() -> None:
    op.drop_index("ix_routine_run_status", table_name="routine_run")
    op.drop_index("ix_routine_run_routine_started", table_name="routine_run")
    op.drop_table("routine_run")

    op.drop_index("ix_routine_enabled", table_name="routine")
    op.drop_table("routine")

    # Drop views that reference `task` before the SQLite batch_alter rebuild
    # — otherwise the rename leg ("ALTER _alembic_tmp_task RENAME TO task")
    # trips on the dangling view definition. We re-create the same three
    # views afterwards (definitions copied verbatim from migrations 0002 +
    # 0006).
    op.execute("DROP VIEW IF EXISTS section_totals")
    op.execute("DROP VIEW IF EXISTS plan_totals")
    op.execute("DROP VIEW IF EXISTS ready_tasks")

    op.drop_index("ix_task_checked_out_by", table_name="task")
    with op.batch_alter_table("task") as batch:
        batch.drop_column("expected_status_at_checkout")
        batch.drop_column("checked_out_at")
        batch.drop_column("checked_out_by")

    op.execute("""
        CREATE VIEW section_totals AS
        SELECT
          s.row_id AS section_id,
          COUNT(t.row_id) AS tasks_total,
          COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
          COALESCE(SUM(CASE WHEN t.status = 'in-progress' THEN 1 ELSE 0 END), 0) AS tasks_in_progress
        FROM plan_section s
        LEFT JOIN task t ON t.section_id = s.row_id
        GROUP BY s.row_id
    """)
    op.execute("""
        CREATE VIEW plan_totals AS
        SELECT
          p.row_id AS plan_id,
          COUNT(t.row_id) AS tasks_total,
          COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
          COALESCE(SUM(CASE WHEN t.status = 'in-progress' THEN 1 ELSE 0 END), 0) AS tasks_in_progress
        FROM plan p
        LEFT JOIN task t ON t.plan_id = p.row_id
        GROUP BY p.row_id
    """)
    op.execute("""
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
    """)
