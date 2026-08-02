"""Commit links — git SHAs referencing task IDs (OBI-010).

Mirrors migration ``0020_commit_links``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class CommitLinkModel(Base):
    """One (project, task_id, sha) edge from a parsed git log."""

    __tablename__ = "commit_link"
    __table_args__ = (
        UniqueConstraint("project_id", "task_id", "sha", name="uq_commit_link_edge"),
        Index("ix_commit_link_task", "project_id", "task_id"),
        Index("ix_commit_link_sha", "project_id", "sha"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(32), nullable=False)
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    short_sha: Mapped[str] = mapped_column(String(12), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
