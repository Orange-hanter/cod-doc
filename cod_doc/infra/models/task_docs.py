"""Task-bound documents (PCA-100, proposal 05).

Each (task_id, key) pair is a unique task document. Revisions are tracked
through the shared `revision` table (entity_kind='task_doc'). Optimistic
locking is done by the service layer via `current_revision_id`.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class TaskDocumentModel(Base):
    """One structured document pinned to a task.

    Canonical keys: 'plan', 'design', 'verification', 'acceptance', or custom.
    Body is stored inline (markdown). Revision trail is in the `revision` table
    with entity_kind='task_doc', entity_id=TaskDocumentModel.row_id.
    """

    __tablename__ = "task_document"
    __table_args__ = (
        UniqueConstraint("task_id", "key", name="uq_task_document_task_key"),
        Index("ix_task_document_task_id", "task_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task.row_id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    format: Mapped[str] = mapped_column(String(16), nullable=False, default="markdown")
    current_revision_id: Mapped[str | None] = mapped_column(String(26))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
