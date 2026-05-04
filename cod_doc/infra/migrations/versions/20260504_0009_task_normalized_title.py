"""COD-076: materialize task.normalized_title for indexed duplicate detection.

Before this migration ``task_service.find_duplicate_by_title`` did a
sequential scan of every project task and normalized in Python. With ten
thousand tasks per project that's prohibitive on the create-task hot path.

Adds:
- column ``task.normalized_title`` (Text, nullable) — backfilled in this
  migration via the same ``cod_doc.domain.text.normalize_title`` helper
  the service uses, so old + new rows share one canonical form;
- index ``ix_task_project_normtitle`` on ``(project_id, normalized_title)``
  — non-unique, since `allow_duplicate=True` flow can legitimately store
  duplicates intentionally.

Revision ID: 0009_task_normalized_title
Revises: 0008_trace_call
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

from cod_doc.domain.text import normalize_title

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0009_task_normalized_title"
down_revision: str | None = "0008_trace_call"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("task") as batch:
        batch.add_column(sa.Column("normalized_title", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT row_id, title FROM task")).all()
    for row in rows:
        bind.execute(
            sa.text("UPDATE task SET normalized_title = :nt WHERE row_id = :rid"),
            {"nt": normalize_title(row.title or ""), "rid": row.row_id},
        )

    op.create_index(
        "ix_task_project_normtitle",
        "task",
        ["project_id", "normalized_title"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_project_normtitle", table_name="task")
    with op.batch_alter_table("task") as batch:
        batch.drop_column("normalized_title")
