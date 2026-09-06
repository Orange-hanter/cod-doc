"""Activity events — unified audit timeline (PCA-110, proposal 09).

Append-only. Every MCP write-tool emits one or more events here so
operators can ask "what changed, when, and who triggered it" without
correlating separate tables.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class ActivityEventModel(Base):
    """Single activity event row.

    ``id`` is a UUID7 string (sortable by creation time). ``ts`` is the
    event timestamp (usually == created, but can be overridden for
    back-dated imports). ``run_id`` links to ``agent_run.run_id`` — NULL
    for direct human/CLI actions.
    """

    __tablename__ = "activity_event"
    __table_args__ = (
        Index("ix_activity_event_ts", "ts"),
        Index("ix_activity_event_scope", "scope_kind", "scope_id", "ts"),
        Index("ix_activity_event_run", "run_id"),
        Index("ix_activity_event_project_ts", "project_id", "ts"),
        Index("ix_activity_event_kind", "kind", "ts"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # UUID7 for time-sortable, globally unique event identity.
    id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    # actor_kind: домен-словарь domain.entities.ActorKind —
    # 'human' | 'agent' | 'orchestrator' | 'routine' | 'system' | 'cli' | 'api'.
    # Выводить из строки-автора только через actor_kind_for_author (ADR-012).
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # actor_id: user identifier, agent run_id, routine name, etc.
    actor_id: Mapped[str | None] = mapped_column(String(128))
    # run_id from agent_run table; NULL for direct human actions.
    run_id: Mapped[str | None] = mapped_column(String(36))
    # Canonical event kind — see proposal 09 for the full taxonomy.
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # Scope of the event: 'task' | 'doc' | 'story' | 'project' | 'task_doc' | 'approval' | 'run'
    scope_kind: Mapped[str | None] = mapped_column(String(32))
    # Human-readable scope identifier (task_id, doc_key, story_id, …).
    scope_id: Mapped[str | None] = mapped_column(String(255))
    # Structured payload typed per kind (JSON).
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    # Human-readable one-liner for UI timelines.
    summary: Mapped[str | None] = mapped_column(Text)
