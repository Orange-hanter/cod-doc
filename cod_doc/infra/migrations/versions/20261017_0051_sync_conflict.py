"""0051: таблица конфликтов слияния ``sync_conflict``.

RFC 29 §3.7 (схема), §10.1 (колонка ``kind``) — CLO-017.

Конфликт — строка таблицы, а не маркеры в тексте: pull кладёт сюда то, что не
сводится автоматически (пересекающиеся ханки текста, недопустимая пара
статусов, правка под удалённым родителем, склейка ключей с разными телами,
двойной офлайн-checkout), ``sync resolve`` разрешает. Таблица ``local`` в
``sync_registry``: конфликт — состояние этой реплики, разрешение уезжает
обычной операцией над сущностью.

Только ``CREATE TABLE`` и индекс: ни одна существующая таблица не
пересоздаётся. Downgrade — ``DROP TABLE`` (открытые конфликты теряются: их
нет в схеме 0050).

Revision ID: 0051_sync_conflict
Revises: 0050_sync_rank
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0051_sync_conflict"
down_revision: str | None = "0050_sync_rank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_conflict",
        sa.Column("uid", sa.Text(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("entity_uid", sa.Text(), nullable=False),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ours", sa.Text(), nullable=True),
        sa.Column("theirs", sa.Text(), nullable=True),
        sa.Column("base", sa.Text(), nullable=True),
        sa.Column("theirs_op", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('field','text','status','delete_edit','key_merge','double_checkout')",
            name="ck_sync_conflict_kind",
        ),
        sa.CheckConstraint("status IN ('open','resolved')", name="ck_sync_conflict_status"),
    )
    op.create_index("ix_sync_conflict_status", "sync_conflict", ["project_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_sync_conflict_status", table_name="sync_conflict")
    op.drop_table("sync_conflict")
