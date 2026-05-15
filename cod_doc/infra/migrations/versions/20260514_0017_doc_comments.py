"""doc_comment table — section-anchored and document-level review comments.

Comments are user notes attached to sections (Google-Docs-style side bubbles)
or to a whole document. They live as meta on the document and can be
batch-applied via AI to rewrite the underlying sections.

Revision ID: 0017_doc_comments
Revises: 0016_document_sha256
Create Date: 2026-05-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_doc_comments"
down_revision = "0016_document_sha256"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "doc_comment",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.Integer,
            sa.ForeignKey("document.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "section_id",
            sa.Integer,
            sa.ForeignKey("section.row_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("anchor", sa.String(255), nullable=True),
        sa.Column("quote", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("author", sa.String(128), nullable=False, server_default="human:web"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column(
            "created",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index("ix_doc_comment_document", "doc_comment", ["document_id"])
    op.create_index("ix_doc_comment_section", "doc_comment", ["section_id"])
    op.create_index("ix_doc_comment_status", "doc_comment", ["status"])


def downgrade() -> None:
    op.drop_index("ix_doc_comment_status", table_name="doc_comment")
    op.drop_index("ix_doc_comment_section", table_name="doc_comment")
    op.drop_index("ix_doc_comment_document", table_name="doc_comment")
    op.drop_table("doc_comment")
