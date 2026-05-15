"""OBI-030: repo_file + repo_symbol + repo_import — code-aware file index.

A lightweight project-wide index for "find by symbol" and
"who imports X" queries without grep. Populated by
``cod_doc.services.repo_index_service.scan_project`` via
``cod-doc reindex --files``.

Python files get full symbol / import extraction (AST). Other languages
land as metadata only (path / size / sha1 / language by extension).

Revision ID: 0022_repo_index
Revises: 0021_code_refs
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_repo_index"
down_revision = "0021_code_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repo_file",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(512), nullable=False),
        sa.Column("language", sa.String(32), nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sha1", sa.String(40), nullable=False),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint("project_id", "path", name="uq_repo_file_project_path"),
    )
    op.create_index("ix_repo_file_lang", "repo_file", ["project_id", "language"])

    op.create_table(
        "repo_symbol",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "file_id",
            sa.Integer,
            sa.ForeignKey("repo_file.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("line", sa.Integer, nullable=False),
        sa.Column("parent_name", sa.String(255), nullable=True),
    )
    op.create_index("ix_repo_symbol_file", "repo_symbol", ["file_id"])
    op.create_index("ix_repo_symbol_name", "repo_symbol", ["name"])

    op.create_table(
        "repo_import",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "file_id",
            sa.Integer,
            sa.ForeignKey("repo_file.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("module", sa.String(255), nullable=False),
        sa.Column("line", sa.Integer, nullable=False),
    )
    op.create_index("ix_repo_import_file", "repo_import", ["file_id"])
    op.create_index("ix_repo_import_module", "repo_import", ["module"])


def downgrade() -> None:
    op.drop_index("ix_repo_import_module", table_name="repo_import")
    op.drop_index("ix_repo_import_file", table_name="repo_import")
    op.drop_table("repo_import")
    op.drop_index("ix_repo_symbol_name", table_name="repo_symbol")
    op.drop_index("ix_repo_symbol_file", table_name="repo_symbol")
    op.drop_table("repo_symbol")
    op.drop_index("ix_repo_file_lang", table_name="repo_file")
    op.drop_table("repo_file")
