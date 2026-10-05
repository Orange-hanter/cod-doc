"""ARG-008 (RFC 34 §3.4): полки реестра ADR — ``adr_topic`` и ``adr.topic_id``.

Полка — тема, о чём решение («Хранение», «Агент»), с составом «входит / не
входит» и позицией в списке. У ADR одна полка или ни одной.

``adr.topic_id`` — колонка **без внешнего ключа на уровне БД**, и это
осознанно:

- ``ADD COLUMN`` с ``REFERENCES`` на SQLite проходит, но ``DROP COLUMN`` у
  такой колонки — нет, и ``downgrade`` пришлось бы делать через
  ``batch_alter_table``. Пересоздание ``adr`` уносит по ``ON DELETE CASCADE``
  ``adr_diagram``, ``adr_task``, ``adr_supersedes`` и ``adr_relation`` —
  ровно ловушка из CLAUDE.md про ``document``;
- семантику ``ON DELETE SET NULL`` держит ``adr_topic_service.delete``: перед
  удалением полки её решения уходят в «Без темы» той же транзакцией, с
  ревизией на каждое.

Revision ID: 0046_adr_topics
Revises: 0045_adr_relations
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0046_adr_topics"
down_revision: str | None = "0045_adr_relations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adr_topic",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("includes", sa.Text(), nullable=False, server_default=""),
        sa.Column("excludes", sa.Text(), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_adr_topic_name"),
    )
    op.create_index("ix_adr_topic_project_position", "adr_topic", ["project_id", "position"])
    op.add_column("adr", sa.Column("topic_id", sa.Integer(), nullable=True))
    op.create_index("ix_adr_topic_id", "adr", ["topic_id"])


def downgrade() -> None:
    op.drop_index("ix_adr_topic_id", table_name="adr")
    # Нативный DROP COLUMN (SQLite ≥ 3.35, прецедент 0043), не batch: см. докстринг.
    op.execute("ALTER TABLE adr DROP COLUMN topic_id")
    op.drop_index("ix_adr_topic_project_position", table_name="adr_topic")
    op.drop_table("adr_topic")
