"""DocCommentModel — user-authored review notes pinned to documents/sections.

Two flavours stored in one table:
- section comment: `section_id` populated, `anchor` denormalized for fast
  lookup when rendering bubbles next to the corresponding section.
- document-level comment: `section_id` IS NULL — shown at the bottom of
  the doc as a freeform review zone.

Status lifecycle: `open` → `resolved` (manual dismiss) or `applied`
(picked up by an AI rework pass). Comments are never auto-mutated by
content edits — they survive section body changes; the user resolves
or deletes them explicitly.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class DocCommentModel(Base):
    __tablename__ = "doc_comment"
    __table_args__ = (
        Index("ix_doc_comment_document", "document_id"),
        Index("ix_doc_comment_section", "section_id"),
        Index("ix_doc_comment_status", "status"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("section.row_id", ondelete="CASCADE")
    )
    anchor: Mapped[str | None] = mapped_column(String(255))
    quote: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(128), nullable=False, default="human:web")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
