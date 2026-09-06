"""Revision history + orchestrator run records.

ADR-012: таблица ``audit_log`` удалена (миграция 0033) — журнал
write-операций держит ``activity_event``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class RevisionModel(Base):
    __tablename__ = "revision"
    __table_args__ = (
        Index("ix_revision_entity", "entity_kind", "entity_id", "at"),
        Index("ix_revision_parent", "parent_revision_id"),
        Index("ix_revision_run_id", "run_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(26), nullable=False, unique=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    entity_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(String(26))
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    diff: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    # PCA-030 / ADR-012: телеметрия встроенного оркестратора, не контракт.
    # Заполняется только внутри run_scope / start_orchestrator_run;
    # для мутаций через MCP / CLI / REST всегда NULL.
    run_id: Mapped[str | None] = mapped_column(String(36))


class AgentRunModel(Base):
    """PCA-030: per-orchestrator-heartbeat run record.

    One row per `Orchestrator.run_task` invocation. Mutations made during
    the run carry `run_id` (set as a contextvar in PCA-031), so a single
    SELECT enumerates everything an agent did on that run.

    ADR-012: встроенный раннер — единственный писатель этой таблицы.
    Работа через MCP из Claude Code run-скоуп не открывает, поэтому
    `run_id` на её мутациях NULL. Это телеметрия, а не контракт.
    """

    __tablename__ = "agent_run"
    __table_args__ = (
        Index("ix_agent_run_project_started", "project_id", "started_at"),
        Index("ix_agent_run_status", "status"),
        Index("ix_agent_run_triggering_task", "triggering_task_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wake_reason: Mapped[str | None] = mapped_column(String(32))
    triggering_task_id: Mapped[str | None] = mapped_column(String(32))
    triggering_doc_ref: Mapped[str | None] = mapped_column(String(255))
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    summary: Mapped[str | None] = mapped_column(Text)
