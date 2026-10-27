"""0054: ``plan.rank`` — порядок планов проекта (RFC 36 §3.2.2, ROAD-001).

1. ``ALTER TABLE plan ADD COLUMN rank TEXT`` — только ``ADD COLUMN``: пересборка
   ``plan`` на SQLite унесла бы по CASCADE секции и задачи (тот же механизм,
   что у ``document``, CLAUDE.md).
2. Бэкфилл: внутри ``project_id`` планы получают равномерные ранги в порядке
   ``created, row_id`` — так их до 0054 отдавал ``plan_service.list_for_project``.

Колонка уезжающая (``rank_column`` у ``plan`` в ``sync_registry``), поэтому
миграция открывает эпоху синхронизации (``infra/sync_epochs.py``).

Миграция — застывший снимок: алгоритм ``initial`` — копия, а не импорт
``cod_doc.infra.rank`` (образец 0050).

Downgrade: ``DROP COLUMN rank`` (SQLite ≥ 3.35 правит таблицу на месте).

Revision ID: 0054_plan_rank
Revises: 0053_sync_access
Create Date: 2026-10-27
"""

from __future__ import annotations

from itertools import groupby
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0054_plan_rank"
down_revision: str | None = "0053_sync_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- ранг: застывшая копия cod_doc.infra.rank.initial ------------------------

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_BASE = len(_DIGITS)


def _initial(n: int) -> list[str]:
    width = 2
    while _BASE**width < (n + 1) * _BASE:
        width += 1
    step = _BASE**width // (n + 1)
    ranks = []
    for i in range(n):
        value, digits = step * (i + 1), []
        for _ in range(width):
            value, rem = divmod(value, _BASE)
            digits.append(_DIGITS[rem])
        ranks.append("".join(reversed(digits)).rstrip("0"))
    return ranks


def upgrade() -> None:
    op.execute("ALTER TABLE plan ADD COLUMN rank TEXT")
    bind = op.get_bind()
    rows = bind.exec_driver_sql(
        "SELECT row_id, project_id FROM plan ORDER BY project_id, created, row_id"
    ).fetchall()
    updates: list[dict[str, object]] = []
    for _project, group in groupby(rows, key=lambda row: row[1]):
        ids = [row[0] for row in group]
        updates += [
            {"rank": rank, "row_id": row_id}
            for row_id, rank in zip(ids, _initial(len(ids)), strict=True)
        ]
    if updates:
        bind.execute(sa.text("UPDATE plan SET rank = :rank WHERE row_id = :row_id"), updates)


def downgrade() -> None:
    op.execute("ALTER TABLE plan DROP COLUMN rank")
