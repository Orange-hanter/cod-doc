"""0035: view'ы task-агрегатов учитывают оба написания статуса.

`section_totals` / `plan_totals` считали «в работе» точным сравнением
`status = 'in-progress'`, а `ready_tasks` — «готово к работе» по
`status = 'pending'`. Оба — легаси-написания. С 7-статусной таксономии
(proposal 08) в `task.status` одновременно живут пары
`pending` ≡ `todo` и `in-progress` ≡ `in_progress`: `checkout_service`
пишет каноническое `in_progress`, когда задача пришла из `todo`, и
легаси `in-progress`, когда из `pending`, а `task_update_status`
принимает любое написание.

Следствие: задача, взятая в работу из `todo`, не попадала в
`tasks_in_progress` — прогресс плана показывал её как не начатую; задача
в статусе `todo` не попадала в `ready_tasks` — её не видели
`plan_ready` / `task_next_ready` / overview-страница. Это тот же класс
бага, что ADO-182 закрыл на уровне репозитория
(`equivalent_task_statuses`), — до view'ов он тогда не дошёл.

Здесь сравнение по точной строке заменено на `IN (...)` по всему классу
эквивалентности. Литералы фиксированы намеренно: миграция — застывший
снимок схемы, она не должна меняться вместе с
`domain.entities.TASK_STATUS_ALIASES`. Синхронность стережёт
`tests/infra/test_totals_status_aliases.py`.

Данные не трогаются: пере-создаются только три view'а.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0035_totals_status_aliases"
down_revision: str | None = "0034_story_section"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --------------------------------------------------------------------------- #
# Новые определения — статус сравнивается со всем классом эквивалентности.     #
# --------------------------------------------------------------------------- #

SECTION_TOTALS_NEW = """
CREATE VIEW section_totals AS
SELECT
  s.row_id AS section_id,
  COUNT(t.row_id) AS tasks_total,
  COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
  COALESCE(
    SUM(CASE WHEN t.status IN ('in_progress', 'in-progress') THEN 1 ELSE 0 END), 0
  ) AS tasks_in_progress
FROM plan_section s
LEFT JOIN task t ON t.section_id = s.row_id
GROUP BY s.row_id
"""

PLAN_TOTALS_NEW = """
CREATE VIEW plan_totals AS
SELECT
  p.row_id AS plan_id,
  COUNT(t.row_id) AS tasks_total,
  COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
  COALESCE(
    SUM(CASE WHEN t.status IN ('in_progress', 'in-progress') THEN 1 ELSE 0 END), 0
  ) AS tasks_in_progress
FROM plan p
LEFT JOIN task t ON t.plan_id = p.row_id
GROUP BY p.row_id
"""

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
      AND dep.status <> 'done'
  )
"""


# --------------------------------------------------------------------------- #
# Старые определения — копия из 0027 verbatim, для downgrade.                  #
# --------------------------------------------------------------------------- #

SECTION_TOTALS_OLD = """
CREATE VIEW section_totals AS
SELECT
  s.row_id AS section_id,
  COUNT(t.row_id) AS tasks_total,
  COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
  COALESCE(SUM(CASE WHEN t.status = 'in-progress' THEN 1 ELSE 0 END), 0) AS tasks_in_progress
FROM plan_section s
LEFT JOIN task t ON t.section_id = s.row_id
GROUP BY s.row_id
"""

PLAN_TOTALS_OLD = """
CREATE VIEW plan_totals AS
SELECT
  p.row_id AS plan_id,
  COUNT(t.row_id) AS tasks_total,
  COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
  COALESCE(SUM(CASE WHEN t.status = 'in-progress' THEN 1 ELSE 0 END), 0) AS tasks_in_progress
FROM plan p
LEFT JOIN task t ON t.plan_id = p.row_id
GROUP BY p.row_id
"""

READY_TASKS_OLD = """
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
"""


def _drop_views() -> None:
    op.execute("DROP VIEW IF EXISTS ready_tasks")
    op.execute("DROP VIEW IF EXISTS plan_totals")
    op.execute("DROP VIEW IF EXISTS section_totals")


def upgrade() -> None:
    _drop_views()
    op.execute(SECTION_TOTALS_NEW)
    op.execute(PLAN_TOTALS_NEW)
    op.execute(READY_TASKS_NEW)


def downgrade() -> None:
    _drop_views()
    op.execute(SECTION_TOTALS_OLD)
    op.execute(PLAN_TOTALS_OLD)
    op.execute(READY_TASKS_OLD)
