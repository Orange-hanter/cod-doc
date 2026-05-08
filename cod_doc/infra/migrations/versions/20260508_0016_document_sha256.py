"""document.content_sha256_head — for scan_folder change detection (PCA-928).

Revision ID: 0016_document_sha256
Revises: 0015_link_suggestions
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_document_sha256"
down_revision = "0015_link_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document") as batch:
        batch.add_column(sa.Column("content_sha256_head", sa.String(64), nullable=True))


def downgrade() -> None:
    # SQLite views referencing the document table need to be dropped before
    # batch_alter rebuilds the table; recreate after.
    bind = op.get_bind()
    dialect = bind.dialect.name

    view_defs: list[tuple[str, str]] = []
    if dialect == "sqlite":
        existing = bind.execute(
            sa.text("SELECT name, sql FROM sqlite_master WHERE type='view'")
        ).fetchall()
        for name, sql in existing:
            if sql and "document" in sql.lower():
                view_defs.append((name, sql))
                op.execute(f"DROP VIEW IF EXISTS {name}")

    with op.batch_alter_table("document") as batch:
        batch.drop_column("content_sha256_head")

    for _name, sql in view_defs:
        op.execute(sql)
