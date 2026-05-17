"""Extend ``link`` table with ``to_adr_id`` to support ``LinkKind.ADR``.

A ``[[adr:ADR-NNN]]`` wiki link or a bare ``ADR-NNN`` resolved by the
link service is stored in the existing ``link`` table with
``kind='adr'`` and the target id in this new column. Reusing the link
table — rather than a separate ``adr_link`` table — keeps every
section's outgoing references queryable in one place, on par with
``to_task_id`` / ``to_story_id`` / ``to_file_path``.

Revision ID: 0024_link_adr_ref
Revises: 0023_fts5_index
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_link_adr_ref"
down_revision = "0023_fts5_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("link") as batch:
        batch.add_column(sa.Column("to_adr_id", sa.String(16), nullable=True))
    op.create_index("ix_link_target_adr", "link", ["to_adr_id"])


def downgrade() -> None:
    op.drop_index("ix_link_target_adr", table_name="link")
    with op.batch_alter_table("link") as batch:
        batch.drop_column("to_adr_id")
