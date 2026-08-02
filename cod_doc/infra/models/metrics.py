"""Task metrics — per-completed-task observability (OBI-001).

Mirrors migration ``0019_task_metrics``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Float

from .base import Base


class TaskMetricsModel(Base):
    """One row per completed task; unique on ``task_id`` for idempotent replay."""

    __tablename__ = "task_metrics"
    __table_args__ = (
        Index("ix_task_metrics_project", "project_id"),
        Index("ix_task_metrics_completed", "project_id", "completed_at"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("task.row_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False)
    in_progress_hours: Mapped[float | None] = mapped_column(Float)
    blocked_hours: Mapped[float | None] = mapped_column(Float)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
