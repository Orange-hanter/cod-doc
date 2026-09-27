"""0041: задача с внешним блокером больше не считается готовой (ADO-225).

``task_service.set_blocker`` пишет только ``task.blocked_reason`` и статус
не трогает, а view ``ready_tasks`` на ``blocked_reason`` не смотрела.
«Заблокированная» задача оставалась в ready-множестве — в ``plan_ready``,
``task_next_ready``, ``agent_pick``, блоке Next Batch при экспорте плана.
2026-09-23 AFT-007 пришлось дополнительно переводить в ``status=blocked``
руками, чтобы агент её не взял (находка N2 аудита agent-fit).

Критерий — ``blocked_reason IS NULL``: ровно то же условие, которым
``task_service.list_blocked`` отбирает заблокированные задачи
(``blocked_reason IS NOT NULL``). Задача не может одновременно числиться
заблокированной и готовой; ``clear_blocker`` пишет NULL и тем возвращает
задачу в выборку, если её не держат статус или зависимости.

Данные не трогаются: пере-создаётся один view, таблица ``document`` не
затронута (никакого ``batch_alter_table``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0041_ready_tasks_blocked_reason"
down_revision: str | None = "0040_document_status_resolved_done"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


READY_TASKS_NEW = """
CREATE VIEW ready_tasks AS
SELECT t.*
FROM task t
WHERE t.status IN ('todo', 'pending')
  AND t.blocked_reason IS NULL
  AND NOT EXISTS (
    SELECT 1
    FROM dependency d
    JOIN task dep ON dep.row_id = d.to_task_id
    WHERE d.from_task_id = t.row_id
      AND d.kind = 'blocks'
      AND dep.status NOT IN ('done', 'cancelled')
  )
"""

# Копия определения из 0038 verbatim, для downgrade.
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
      AND dep.status NOT IN ('done', 'cancelled')
  )
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    op.execute(READY_TASKS_NEW)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    op.execute(READY_TASKS_OLD)
