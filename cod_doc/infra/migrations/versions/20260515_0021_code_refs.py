"""OBI-020: extend link table with code-ref columns (file_path + symbol).

Code references like ``[task_service.complete](cod_doc/services/task_service.py#complete)``
are parsed as ``LinkKind.CODE`` and stored in the existing ``link`` table
with ``kind='code'``. The new columns hold the file path and optional
``#symbol`` anchor:

- ``to_file_path`` — relative path inside the project, normalized
  (no leading ``./`` or ``/``).
- ``to_symbol`` — fragment after ``#``; typically a function/class name.

Reusing ``link`` rather than a separate table keeps the resolver/verifier
unified: every section's "outgoing references" — be they docs, tasks, or
code — show up in one query.

Revision ID: 0021_code_refs
Revises: 0020_commit_links
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_code_refs"
down_revision = "0020_commit_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("link") as batch:
        batch.add_column(sa.Column("to_file_path", sa.String(512), nullable=True))
        batch.add_column(sa.Column("to_symbol", sa.String(128), nullable=True))
    op.create_index("ix_link_target_file", "link", ["to_file_path"])


def downgrade() -> None:
    op.drop_index("ix_link_target_file", table_name="link")
    with op.batch_alter_table("link") as batch:
        batch.drop_column("to_symbol")
        batch.drop_column("to_file_path")
