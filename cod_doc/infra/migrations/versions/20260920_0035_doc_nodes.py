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


# ``batch_alter_table`` на SQLite пересоздаёт таблицу через
# ``_alembic_tmp_document`` и RENAME. На ``document`` висит view
# ``document_body``, и RENAME падает с «error in view document_body: no such
# table: main.document». Поэтому view снимается на время перестройки и
# восстанавливается следом.
#
# Текст продублирован из 0030, а не импортирован оттуда, по той же причине, по
# которой 0030 дословно повторяет форму 0025: миграция — снимок схемы на свой
# момент, и она обязана пережить любую последующую правку соседнего файла.
# От текста этого view считается ``document.projection_hash``, так что
# расхождение здесь молча испортило бы хэши всего корпуса.
DOCUMENT_BODY_VIEW_SQLITE = """
CREATE VIEW document_body AS
SELECT
  document_id,
  preamble
    || CASE WHEN preamble <> '' AND sec <> '' THEN char(10) || char(10) ELSE '' END
    || sec AS body
FROM (
  SELECT
    d.row_id AS document_id,
    d.preamble AS preamble,
    COALESCE((
      SELECT group_concat(
        substr('######', 1, s.level) || ' ' || s.heading || char(10) || char(10) || s.body,
        char(10) || char(10)
      )
      FROM (
        SELECT level, heading, body
        FROM section
        WHERE document_id = d.row_id
        ORDER BY position
      ) s
    ), '') AS sec
  FROM document d
) AS t
"""

DOCUMENT_BODY_VIEW_POSTGRES = """
CREATE VIEW document_body AS
SELECT
  document_id,
  preamble
    || CASE WHEN preamble <> '' AND sec <> '' THEN E'\\n\\n' ELSE '' END
    || sec AS body
FROM (
  SELECT
    d.row_id AS document_id,
    d.preamble AS preamble,
    COALESCE((
      SELECT string_agg(
        repeat('#', s.level) || ' ' || s.heading || E'\\n\\n' || s.body,
        E'\\n\\n'
        ORDER BY s.position
      )
      FROM section s
      WHERE s.document_id = d.row_id
    ), '') AS sec
  FROM document d
) AS t
"""


def _document_body_view() -> str:
    is_sqlite = op.get_bind().dialect.name == "sqlite"
    return DOCUMENT_BODY_VIEW_SQLITE if is_sqlite else DOCUMENT_BODY_VIEW_POSTGRES


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

    # batch_alter_table: SQLite не умеет ADD COLUMN с FK нативно. Имя
    # constraint'а задаём явно — иначе downgrade не сможет его снять.
    op.execute("DROP VIEW IF EXISTS document_body")
    with op.batch_alter_table("document") as batch:
        batch.add_column(sa.Column("node_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("node_position", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_document_node",
            "doc_node",
            ["node_id"],
            ["row_id"],
            ondelete="SET NULL",
        )
    op.execute(_document_body_view())
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

    op.drop_index("ix_document_node", table_name="document")
    op.execute("DROP VIEW IF EXISTS document_body")
    with op.batch_alter_table("document") as batch:
        batch.drop_constraint("fk_document_node", type_="foreignkey")
        batch.drop_column("node_position")
        batch.drop_column("node_id")
    op.execute(_document_body_view())

    op.drop_index("ix_doc_node_parent", table_name="doc_node")
    op.drop_index("ix_doc_node_project", table_name="doc_node")
    op.drop_table("doc_node")
