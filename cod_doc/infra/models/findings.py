"""Finding + FindingSourceRun + ExternalRef ORM models (RFC 22 §3.2)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class FindingModel(Base):
    __tablename__ = "finding"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source", "fingerprint", name="uq_finding_project_source_fp"
        ),
        Index("ix_finding_project_id", "project_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    finding_uid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    line: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default=text("'open'")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    times_seen: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    promoted_task_id: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    run_id: Mapped[str | None] = mapped_column(String(36))


class FindingSourceRunModel(Base):
    __tablename__ = "finding_source_run"
    __table_args__ = (Index("ix_finding_source_run_finding_id", "finding_id"),)

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("finding.row_id", ondelete="CASCADE"), nullable=False
    )
    source_run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    severity_at_run: Mapped[str | None] = mapped_column(String(16))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ExternalRefModel(Base):
    __tablename__ = "external_ref"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "system", "external_id", name="uq_external_ref_project_system_id"
        ),
        Index("ix_external_ref_project_id", "project_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    entity_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_row_id: Mapped[int] = mapped_column(Integer, nullable=False)
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
