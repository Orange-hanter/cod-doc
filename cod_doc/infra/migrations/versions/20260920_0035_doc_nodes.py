"""Дерево документации как данные: ``doc_node`` + ``document.node_id``.

До этой миграции иерархии документов в БД не было вовсе: ни родителя, ни
порядка, ни уровня. Дерево на экране ``/p/<slug>/docs`` строилось в веб-слое
из ``doc_key.split("/")`` — то есть показывало файловую кучу, а не
информационную архитектуру. На корпусе cod-doc это давало 14 папок
``cod_doc/skills/*`` с одним файлом внутри каждой и узел ``audit`` на 42
документа.

``doc_node`` — тот же паттерн, что ``plan_section`` (0002) и ``story_section``
(0034): стабильный ключ, человеческое название, явный порядок. Отличий два.

``parent_id`` на себя — чтобы крупный раздел (``audit``, ``proposals``) можно
было разделить на подузлы без новой миграции. Дефолтное дерево сеется плоским;
глубина остаётся возможностью, а не обязанностью.

``intent`` — проза о том, что в разделе должно лежать. Её читают оба: человек
в шапке списка и агент-куратор, когда предлагает раскладку. Без неё раздел —
голый ярлык, и «что сюда класть» приходится угадывать по названию.

Бэкфила здесь нет намеренно. Все существующие строки получают
``node_id IS NULL`` и честно оказываются в Инбоксе: раскладка корпуса — это
продуктовое решение, а не побочный эффект ``alembic upgrade``. Её выполняет
явная команда ``cod-doc doc tree classify`` с ``--dry-run``.

``document.node_id`` — nullable и ``ON DELETE SET NULL``: удаление раздела
обязано осиротить документ, но не удалить его вместе с секциями и ссылками.
Документ без раздела остаётся валидным и показывается в Инбоксе.

Revision ID: 0035_doc_nodes
Revises: 0034_story_section
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0035_doc_nodes"
down_revision: str | None = "0034_story_section"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Колонки добавляются обычным ``ALTER TABLE ADD COLUMN``, **не**
# ``batch_alter_table``, и это не стилистический выбор.
#
# На SQLite batch-режим пересоздаёт таблицу: копия в ``_alembic_tmp_document``,
# DROP старой, RENAME. При включённом ``PRAGMA foreign_keys`` DROP таблицы
# ``document`` уносит по ``ON DELETE CASCADE`` все её ``section``, а следом —
# все ``link``, которые висят на секциях. Проверено на копии живой БД: 170
# документов пережили перестройку, а 1379 секций и 841 ссылка исчезли. На
# пустой тестовой БД такая миграция зеленеет: терять там нечего.
#
# Заодно отпадает и возня с view ``document_body``: он ссылается на
# ``document``, и RENAME ронял его с «no such table: main.document».
#
# ``ALTER TABLE ADD COLUMN`` c ``REFERENCES`` SQLite поддерживает, если у
# колонки дефолт NULL — ровно наш случай. Ничего не перестраивается, данные не
# трогаются.
_NODE_FK = "fk_document_node"


def upgrade() -> None:
    op.create_table(
        "doc_node",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_key", sa.String(64), nullable=False),
        # Самоссылка CASCADE: удаление родителя уносит поддерево разделов, но
        # документы из него не трогает — их отвязывает SET NULL на document.
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("doc_node.row_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False),
        # Подсказка классификатору и валидатору «что здесь ожидается», а не
        # ограничение: раздел не обязан отвергать документ другого типа.
        sa.Column("expected_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("min_docs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_inbox", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("project_id", "node_key", name="uq_doc_node_project_key"),
    )
    op.create_index("ix_doc_node_project", "doc_node", ["project_id", "position"])
    op.create_index("ix_doc_node_parent", "doc_node", ["parent_id"])

    if op.get_bind().dialect.name == "sqlite":
        # Alembic не умеет отдать FK внутри ``add_column``: он всегда выносит
        # его в отдельный ALTER TABLE ADD CONSTRAINT, которого у SQLite нет, и
        # предлагает batch-режим — тот самый, что уносит секции и ссылки.
        # Inline-``REFERENCES`` в ADD COLUMN SQLite поддерживает сам, поэтому
        # здесь DDL пишется руками.
        op.execute(
            "ALTER TABLE document ADD COLUMN node_id INTEGER "
            "REFERENCES doc_node (row_id) ON DELETE SET NULL"
        )
    else:
        op.add_column("document", sa.Column("node_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            _NODE_FK, "document", "doc_node", ["node_id"], ["row_id"], ondelete="SET NULL"
        )
    op.add_column("document", sa.Column("node_position", sa.Integer(), nullable=True))
    op.create_index("ix_document_node", "document", ["node_id", "node_position"])

    op.create_table(
        "doc_node_suggestion",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("document.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("doc_node.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "node_id", name="uq_doc_node_suggestion"),
    )
    op.create_index("ix_doc_node_suggestion_state", "doc_node_suggestion", ["state", "document_id"])


def downgrade() -> None:
    op.drop_index("ix_doc_node_suggestion_state", table_name="doc_node_suggestion")
    op.drop_table("doc_node_suggestion")

    # Симметрично upgrade'у: обычный DROP COLUMN, без перестройки таблицы.
    # SQLite ≥ 3.35 умеет его нативно; индекс снимаем первым, иначе колонка
    # считается занятой. FK уходит вместе с колонкой — отдельного DROP
    # CONSTRAINT тут не нужно и на SQLite не существует.
    op.drop_index("ix_document_node", table_name="document")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(_NODE_FK, "document", type_="foreignkey")
    op.execute("ALTER TABLE document DROP COLUMN node_position")
    op.execute("ALTER TABLE document DROP COLUMN node_id")

    op.drop_index("ix_doc_node_parent", table_name="doc_node")
    op.drop_index("ix_doc_node_project", table_name="doc_node")
    op.drop_table("doc_node")
