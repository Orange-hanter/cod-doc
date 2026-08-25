"""TaskMetricsModel — per-task completion stats (OBI-001)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TaskMetricsModel(Base):
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
        Integer,
        ForeignKey("project.row_id", ondelete="CASCADE"),
        nullable=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    duration_hours: Mapped[float] = mapped_column(Float, nullable=False)
    in_progress_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    blocked_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
