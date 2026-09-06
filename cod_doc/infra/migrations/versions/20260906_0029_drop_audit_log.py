"""ADO-044 / ADR-012: drop the never-written ``audit_log`` table.

`audit_log` был объявлен в ARCHITECTURE §9 и DATA_MODEL §3.13 как журнал
write-операций, но за всю историю проекта не получил ни одного writer'а
(0 строк во всех известных БД). Потребность закрывает `activity_event`.

`downgrade()` восстанавливает таблицу ровно в том виде, в каком её
оставили миграции 0004 (колонки + два индекса), 0006 (`server_default`
на `payload_json`) и 0010 (`run_id` + `ix_audit_log_run_id`).

Revision ID: 0029_drop_audit_log
Revises: 0028_findings
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0029_drop_audit_log"
down_revision: str | None = "0028_findings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_audit_log_run_id", table_name="audit_log")
    op.drop_index("ix_audit_actor", table_name="audit_log")
    op.drop_index("ix_audit_action", table_name="audit_log")
    op.drop_table("audit_log")


def downgrade() -> None:
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
        sa.Column(
            "payload_json",
            sa.JSON,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("result", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        # 0010: linkage to agent_run; NULL = human/external mutation.
        sa.Column("run_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_audit_action", "audit_log", ["action", "at"])
    op.create_index("ix_audit_actor", "audit_log", ["actor", "at"])
    op.create_index("ix_audit_log_run_id", "audit_log", ["run_id"])
