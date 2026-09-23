"""0039: агрегаты плана считают отменённые задачи отдельной колонкой.

`section_totals` / `plan_totals` отдавали три числа — `tasks_total`,
`tasks_done`, `tasks_in_progress`. «Сколько осталось» сервисный слой выводил
как `total - done` (`plan_service/_types.py`), и отменённая задача попадала в
остаток навсегда: на живом плане `adoption-2026-08` четыре `cancelled`
третий месяц числились несделанными, а план с восемью закрытыми и двумя
отменёнными задачами из десяти висел в `in-progress` без единой задачи,
которую можно взять.

Почему колонка во view, а не подсчёт в Python. Числа плана уже агрегируются
здесь, и другого места у них нет: `recalc` читает обе вьюхи, а
`recalc_for_project` — одним запросом на весь проект (COD-075 убирал там
N+1). Считать `cancelled` отдельным запросом значило бы завести четвёртое
определение «сколько чего в плане» рядом с тремя существующими — ровно ту
рассинхронизацию, которую 0035 и 0038 только что убирали из соседних
выражений.

`cancelled` — каноническое написание без легаси-пары
(`domain.entities.TASK_STATUS_ALIASES` знает только `pending` и
`in-progress`), поэтому сравнение точной строкой здесь не режет класс
эквивалентности. Литералы захардкожены намеренно: миграция — застывший
снимок схемы, она не должна ехать вместе с `TERMINAL_STATUSES`.
Синхронность стережёт `tests/infra/test_totals_cancelled.py`.

Новая колонка добавлена последней: позиционные `SELECT *` по этим вьюхам
сохраняют прежний порядок полей.

Данные не трогаются: пере-создаются два view.

Revision ID: 0039_totals_cancelled
Revises: 0038_ready_tasks_cancelled_is_closed
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0039_totals_cancelled"
down_revision: str | None = "0038_ready_tasks_cancelled_is_closed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SECTION_TOTALS_NEW = """
CREATE VIEW section_totals AS
SELECT
  s.row_id AS section_id,
  COUNT(t.row_id) AS tasks_total,
  COALESCE(SUM(CASE WHEN t.status = 'done' THEN 1 ELSE 0 END), 0) AS tasks_done,
  COALESCE(
    SUM(CASE WHEN t.status IN ('in_progress', 'in-progress') THEN 1 ELSE 0 END), 0
  ) AS tasks_in_progress,
  COALESCE(SUM(CASE WHEN t.status = 'cancelled' THEN 1 ELSE 0 END), 0) AS tasks_cancelled
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
  ) AS tasks_in_progress,
  COALESCE(SUM(CASE WHEN t.status = 'cancelled' THEN 1 ELSE 0 END), 0) AS tasks_cancelled
FROM plan p
LEFT JOIN task t ON t.plan_id = p.row_id
GROUP BY p.row_id
"""


# --------------------------------------------------------------------------- #
# Старые определения — копия из 0035 verbatim, для downgrade.                  #
# --------------------------------------------------------------------------- #

SECTION_TOTALS_OLD = """
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

PLAN_TOTALS_OLD = """
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


def _drop_views() -> None:
    op.execute("DROP VIEW IF EXISTS plan_totals")
    op.execute("DROP VIEW IF EXISTS section_totals")


def upgrade() -> None:
    _drop_views()
    op.execute(SECTION_TOTALS_NEW)
    op.execute(PLAN_TOTALS_NEW)


def downgrade() -> None:
    _drop_views()
    op.execute(SECTION_TOTALS_OLD)
    op.execute(PLAN_TOTALS_OLD)
