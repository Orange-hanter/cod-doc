"""Routine + RoutineRun — cron-style health checks (PCA-210, proposal 07).

Each routine is a parametrised invocation of a check function (defined
in :mod:`cod_doc.services.routine_catalog`). Each invocation produces a
``routine_run`` row and, optionally, an ``activity_event`` and/or a new
task (per ``on_finding`` policy).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class RoutineModel(Base):
    """A scheduled or manually-triggered check.

    Triggers: 'cron' | 'manual' | 'event'.
    on_finding: 'create_task' | 'update_existing_task' | 'comment_only'.
    concurrency: 'skip' | 'queue' | 'parallel'.
    catch_up: 'skip' | 'run_latest' | 'run_all'.
    """

    __tablename__ = "routine"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_routine_project_name"),
        Index("ix_routine_enabled", "enabled"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="cron")
    cron: Mapped[str | None] = mapped_column(String(64))
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    check_args: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    on_finding: Mapped[str] = mapped_column(String(32), nullable=False, default="comment_only")
    concurrency: Mapped[str] = mapped_column(String(8), nullable=False, default="skip")
    catch_up: Mapped[str] = mapped_column(String(16), nullable=False, default="run_latest")
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class RoutineRunModel(Base):
    """One invocation of a routine.

    status: 'running' | 'done' | 'failed' | 'skipped'.
    """

    __tablename__ = "routine_run"
    __table_args__ = (
        Index("ix_routine_run_routine_started", "routine_id", "started_at"),
        Index("ix_routine_run_status", "status"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    routine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("routine.row_id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    findings_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_task_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(36))
