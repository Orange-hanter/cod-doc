"""0058: граф связей — таблицы ``relation`` и ``relation_type`` (RFC 39 §3.3).

Обе таблицы authored и открывают эпоху синхронизации (уезжающие таблицы).
Концы ребра адресуются ``uid`` строки-цели, FK на концах нет: удаление
сущности обрабатывает событие, а не CASCADE. ``to_uid`` NULL — конец
``code:``/``url:`` или ссылка вперёд (хранится литералом в ``to_key``).

``uid`` — ``TEXT NULL``, как у остальных authored-таблиц (``SyncUidMixin``):
NOT NULL обеспечивает захват, а детерминированный uuid5 ребра длиннее 26
символов, которые давал RFC. ``uq_<t>_uid`` создаётся здесь же, как в 0049.

Существующие таблицы (в том числе ``document``) не трогаются.

Revision ID: 0058_relation_graph
Revises: 0057_responder_role
Create Date: 2026-11-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0058_relation_graph"
down_revision: str | None = "0057_responder_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROJECT_FK = "project.row_id"


def upgrade() -> None:
    op.create_table(
        "relation",
        sa.Column("row_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Text(), nullable=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey(_PROJECT_FK, ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_kind", sa.String(16), nullable=False),
        sa.Column("from_uid", sa.Text(), nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("to_kind", sa.String(16), nullable=False),
        sa.Column("to_uid", sa.Text(), nullable=True),
        sa.Column("to_key", sa.String(512), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("broken_reason", sa.String(32), nullable=True),
        sa.Column("last_checked", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "to_uid IS NOT NULL OR to_key IS NOT NULL", name="ck_relation_has_target"
        ),
        sa.UniqueConstraint(
            "project_id", "from_uid", "type", "to_kind", "to_uid", "to_key", name="uq_relation_edge"
        ),
    )
    op.create_index("uq_relation_uid", "relation", ["uid"], unique=True)
    # NULL в UNIQUE не равен NULL: дубли литеральных концов страхует частичный индекс.
    op.create_index(
        "uq_relation_edge_literal",
        "relation",
        ["project_id", "from_uid", "type", "to_kind", "to_key"],
        unique=True,
        sqlite_where=sa.text("to_uid IS NULL"),
        postgresql_where=sa.text("to_uid IS NULL"),
    )
    op.create_index("ix_relation_to", "relation", ["project_id", "to_uid"])
    op.create_index("ix_relation_key", "relation", ["project_id", "to_kind", "to_key"])
    op.create_index("ix_relation_from", "relation", ["project_id", "from_uid"])

    op.create_table(
        "relation_type",
        sa.Column("row_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Text(), nullable=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey(_PROJECT_FK, ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("inverse", sa.String(48), nullable=False),
        sa.Column("from_kinds", sa.JSON(), nullable=False),
        sa.Column("to_kinds", sa.JSON(), nullable=False),
        sa.Column("symmetric", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acyclic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allow_forward", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("project_id", "name", name="uq_relation_type_name"),
    )
    op.create_index("uq_relation_type_uid", "relation_type", ["uid"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_relation_type_uid", table_name="relation_type")
    op.drop_table("relation_type")
    op.drop_index("ix_relation_from", table_name="relation")
    op.drop_index("ix_relation_key", table_name="relation")
    op.drop_index("ix_relation_to", table_name="relation")
    op.drop_index("uq_relation_edge_literal", table_name="relation")
    op.drop_index("uq_relation_uid", table_name="relation")
    op.drop_table("relation")
