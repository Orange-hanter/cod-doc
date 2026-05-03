"""COD-055: structured blocked_reason on Task.

Adds a free-text ``blocked_reason`` column to the ``task`` table — used to
record *external* blockers (waiting on a stakeholder decision, missing spec,
…) that are NOT another task. Internal task-to-task blockers stay in the
``dependency`` edge graph.

A task is considered "blocked" iff blocked_reason IS NOT NULL AND status =
'pending' or 'in-progress'.

Revision ID: 0007_task_blocked_reason
Revises: 0006_views_and_defaults
Create Date: 2026-05-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0007_task_blocked_reason"
down_revision: str | None = "0006_views_and_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("blocked_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.drop_column("blocked_reason")
