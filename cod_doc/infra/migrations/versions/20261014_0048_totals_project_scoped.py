"""0048: агрегаты плана считают только задачи проекта плана.

`section_totals` / `plan_totals` присоединяли задачи по `section_id` /
`plan_id` и не сверяли проект. В общей hub-БД задача другого проекта с
`plan_id` / `section_id` нашего плана (легаси-данные, ручная правка, ремаппинг
при переносе) попадала в счётчики прогресса — хотя `audit`, `export`,
`chain_layout`, `critical_path` и `ready_batch` с AFT-023 такую задачу уже не
видят. План показывал бы «5 из 7», где две задачи — чужие и в этом плане
недоступны ни одной поверхности.

Условие стоит в `ON` левого соединения, а не в `WHERE`: раздел или план без
единой своей задачи остаётся строкой с нулями, как и раньше. Проект плана
секция берёт через `plan` (у `plan_section` своего `project_id` нет).

Определения статусов — дословно из 0039: миграция — застывший снимок схемы.
Порядок колонок прежний: позиционные `SELECT *` по вьюхам не сдвигаются.

Данные не трогаются: пере-создаются два view (AFT-024).

Revision ID: 0048_totals_project_scoped
Revises: 0047_project_scoped_uniques
Create Date: 2026-10-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0048_totals_project_scoped"
down_revision: str | None = "0047_project_scoped_uniques"
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
JOIN plan p ON p.row_id = s.plan_id
LEFT JOIN task t ON t.section_id = s.row_id AND t.project_id = p.project_id
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
LEFT JOIN task t ON t.plan_id = p.row_id AND t.project_id = p.project_id
GROUP BY p.row_id
"""


# --------------------------------------------------------------------------- #
# Downgrade: определения 0039 дословно.                                       #
# --------------------------------------------------------------------------- #

SECTION_TOTALS_OLD = """
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

PLAN_TOTALS_OLD = """
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
