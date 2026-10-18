"""0052: ``sync_conflict.opened_hlc`` — штамп конфликта для маркера разрешения.

RFC 29 §3.7 — CLO-045. Разрешение конфликта на одной реплике пишет маркер
(операция ``sync_op`` вида ``sync_resolution``), и получатель закрывает свои
конфликты той же подписи, открытые раньше маркера. «Раньше» — по
``opened_hlc``: старшему штампу записей, сошедшихся в конфликте.

Одна nullable-колонка local-таблицы: ``NULL`` у конфликтов, открытых до этой
миграции, — они старше любого маркера. ``ADD COLUMN`` таблицу не
пересоздаёт; downgrade — ``batch_alter_table`` над ``sync_conflict`` (на неё
никто не ссылается, каскадов нет), значения колонки теряются.

Revision ID: 0052_sync_conflict_opened_hlc
Revises: 0051_sync_conflict
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0052_sync_conflict_opened_hlc"
down_revision: str | None = "0051_sync_conflict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sync_conflict", sa.Column("opened_hlc", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sync_conflict") as batch:
        batch.drop_column("opened_hlc")
