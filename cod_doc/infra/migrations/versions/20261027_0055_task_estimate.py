"""0055: ``task.estimate_person_hours`` — оценка задачи в человеко-часах (RFC 36 §3.2.3, ROAD-009).

``ALTER TABLE task ADD COLUMN estimate_person_hours REAL`` — только ``ADD
COLUMN``: на ``task`` по CASCADE висят ``dependency``, ``affected_file``,
``task_document``, и пересборка таблицы на SQLite их бы унесла. Без ``CHECK``:
``> 0`` проверяет сервис (``task_service.set_estimate``). NULL — оценки нет,
бэкфилла нет.

Колонка уезжающая (``_SHIPPED_BEFORE_UID["task"]`` в ``sync_registry``) —
миграция открывает эпоху синхронизации (``infra/sync_epochs.py``).

Downgrade: ``DROP COLUMN`` (SQLite ≥ 3.35 правит таблицу на месте).

Revision ID: 0055_task_estimate
Revises: 0054_plan_rank
Create Date: 2026-10-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0055_task_estimate"
down_revision: str | None = "0054_plan_rank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE task ADD COLUMN estimate_person_hours REAL")


def downgrade() -> None:
    op.execute("ALTER TABLE task DROP COLUMN estimate_person_hours")
