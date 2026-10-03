"""ADO-243: ``plan.id_prefix`` — префикс ID новых задач плана задаётся явно.

До этой ревизии префикс выводился из самого частого префикса задач плана
(``task_service.id_prefix_for_plan``), а у пустого плана — из ``scope``. Вывод
держался, пока задачи рождались в своём плане. С переносом между планами
(``task_move_to_plan``) он ломается ровно в главном сценарии: 37 задач ``ADO-*``,
переехавших в новый план ``web-ui``, делают следующую новую задачу там ``ADO-…``,
а не ``WEB-…``. Колонка закрепляет префикс за планом; вывод по частоте остаётся
фолбэком для планов, где она пуста.

Nullable, без бэкфилла: у существующих планов поведение не меняется.

``downgrade`` — нативный ``DROP COLUMN`` (SQLite ≥ 3.35, прецедент 0035/0036),
а не ``batch_alter_table``: перестройка ``plan`` на SQLite унесла бы по
``ON DELETE CASCADE`` все секции и задачи плана.

Revision ID: 0043_plan_id_prefix
Revises: 0042_open_questions
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0043_plan_id_prefix"
down_revision: str | None = "0042_open_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan", sa.Column("id_prefix", sa.String(length=5), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE plan DROP COLUMN id_prefix")
