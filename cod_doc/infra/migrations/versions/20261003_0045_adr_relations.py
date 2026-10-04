"""ARG-001 (RFC 34 §3.1): ``adr_relation`` — связи «уточняет» и «опирается на».

До этой ревизии между ADR существовала одна связь — замена
(``adr_supersedes``), и она меняет статус старого решения. Остальные
отношения жили только в прозе: ADR-010 пишет, что уточняет ADR-005 и тот
остаётся в силе; ADR-017 строится на ADR-016. В интерфейсе их не было видно,
а принимать ADR-017 раньше ADR-016 ничто не мешало.

Отдельная таблица, а не колонка ``kind`` в ``adr_supersedes``: замена —
переход статуса с запретом цикла по всему DAG, эти связи статусов не трогают.

Новая таблица, ``adr`` не перестраивается — ``batch_alter_table`` не нужен.

Revision ID: 0045_adr_relations
Revises: 0044_document_type_index_rfc
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0045_adr_relations"
down_revision: str | None = "0044_document_type_index_rfc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adr_relation",
        sa.Column("row_id", sa.Integer(), primary_key=True),
        sa.Column(
            "from_id",
            sa.Integer(),
            sa.ForeignKey("adr.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "to_id",
            sa.Integer(),
            sa.ForeignKey("adr.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("from_id", "to_id", "kind", name="uq_adr_relation_edge"),
        sa.CheckConstraint("from_id <> to_id", name="ck_adr_relation_no_self_loop"),
        sa.CheckConstraint("kind IN ('amends','depends_on')", name="ck_adr_relation_kind"),
    )
    op.create_index("ix_adr_relation_from", "adr_relation", ["from_id"])
    op.create_index("ix_adr_relation_to", "adr_relation", ["to_id"])


def downgrade() -> None:
    op.drop_index("ix_adr_relation_to", table_name="adr_relation")
    op.drop_index("ix_adr_relation_from", table_name="adr_relation")
    op.drop_table("adr_relation")
