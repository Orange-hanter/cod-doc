"""ADO-143: ``story_section`` + ``user_story.section_id``.

До этой миграции у истории не было ни поля секции, ни таблицы секций. Веб
выводил секцию из **прозы нарратива** — регексом, который требовал английскую
формулу ``[US-1.1 Title] As a Role, I want X, so that Y``. На нелатинском
корпусе разбор не срабатывал никогда, и все истории проекта попадали в одну
корзину «Unsorted».

``story_section`` — аналог ``plan_section`` для планов: человеческое название,
явный порядок и стабильный ключ. Ключ ограничен ``[a-z0-9-]+`` на уровне
сервиса: он подставляется в путь роута анализа секции (один сегмент URL) и в
``id``/``hx-target`` htmx-фрагмента, который уходит в ``querySelector``.

``user_story.section_id`` — nullable и ``ON DELETE SET NULL``: удаление секции
обязано осиротить историю, но не удалить её вместе с критериями и связями.
История без секции остаётся валидной и показывается отдельной группой.

Revision ID: 0030_story_section
Revises: 0029_drop_audit_log
Create Date: 2026-09-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0030_story_section"
down_revision: str | None = "0029_drop_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "story_section",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("project_id", "key", name="uq_story_section_project_key"),
    )
    op.create_index("ix_story_section_project", "story_section", ["project_id", "position"])

    # batch_alter_table: SQLite не умеет ADD COLUMN с FK и DROP COLUMN нативно.
    # Имя constraint'а задаём явно — иначе downgrade не сможет его снять.
    with op.batch_alter_table("user_story") as batch:
        batch.add_column(sa.Column("section_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_user_story_section",
            "story_section",
            ["section_id"],
            ["row_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_story") as batch:
        batch.drop_constraint("fk_user_story_section", type_="foreignkey")
        batch.drop_column("section_id")

    op.drop_index("ix_story_section_project", table_name="story_section")
    op.drop_table("story_section")
