"""0038: отменённый блокер больше не держит зависимые задачи.

View ``ready_tasks`` отбирала «пререквизит выполнен» сравнением
``dep.status <> 'done'``. Но закрытых состояний два: статус-машина
(``task_status_machine.is_terminal``) с самого введения 7-статусной
таксономии считает терминальными ``done`` **и** ``cancelled``.

Расхождение стоило дорого и молча: задача, чей блокер отменён, не
попадала в ready-множество никогда — ни в ``plan_ready``, ни в
``task_next_ready``, ни в ``agent_pick``, ни в блок Next Batch при
экспорте плана. Ошибки при этом не было: задача просто не показывалась.
На боевой БД Restate так заперты 4 задачи.

Эталон правильного отбора в дереве уже был — ``agent_service`` исключает
``("done", "cancelled")`` со ссылкой на аудит F2 от 2026-05-15. До view
он тогда не дошёл, как и ADO-182 не дошёл до них со своим классом
эквивалентности (это доделала 0035).

Литералы захардкожены намеренно: миграция — застывший снимок схемы, она
не должна меняться вместе с ``TERMINAL_STATUSES``. Синхронность стережёт
``tests/infra/test_ready_tasks_cancelled.py``.

Данные не трогаются: пере-создаётся один view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0038_ready_tasks_cancelled_is_closed"
down_revision: str | None = "0037_task_status_canonicalisation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Оба закрытых состояния. Зеркалит `task_status_machine.TERMINAL_STATUSES`.
TERMINAL_STATUSES: tuple[str, ...] = ("done", "cancelled")


READY_TASKS_NEW = """
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status IN ('todo', 'pending')
  AND NOT EXISTS (
    SELECT 1
    FROM dependency d
    JOIN task dep ON dep.row_id = d.to_task_id
    WHERE d.from_task_id = t.row_id
      AND d.kind = 'blocks'
      AND dep.status NOT IN ('done', 'cancelled')
  )
"""

# Копия определения из 0035 verbatim, для downgrade.
READY_TASKS_OLD = """
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status IN ('todo', 'pending')
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
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    op.execute(READY_TASKS_NEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    op.execute(READY_TASKS_OLD)
