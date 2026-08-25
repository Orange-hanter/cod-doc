"""ADR + AdrDiagram + AdrSupersede + AdrTask (ADR-001 / cycle-5)."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 — runtime use by Mapped[...]

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _utcnow


class ADRModel(Base):
    __tablename__ = "adr"
    __table_args__ = (
        UniqueConstraint("project_id", "adr_id", name="uq_adr_project_id"),
        CheckConstraint(
            "status IN ('proposed','accepted','superseded','deprecated','rejected')",
            name="ck_adr_status",
        ),
        Index("ix_adr_project_status", "project_id", "status"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    adr_id: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    decided_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternatives: Mapped[str | None] = mapped_column(Text, nullable=True)
    consequences: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String(128), nullable=False, default="human")
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    diagrams: Mapped[list[ADRDiagramModel]] = relationship(
        back_populates="adr",
        cascade="all, delete-orphan",
        order_by="ADRDiagramModel.position",
    )
    task_links: Mapped[list[ADRTaskModel]] = relationship(
        back_populates="adr",
        cascade="all, delete-orphan",
    )


class ADRDiagramModel(Base):
    __tablename__ = "adr_diagram"
    __table_args__ = (
        UniqueConstraint("adr_id", "position", name="uq_adr_diagram_position"),
        Index("ix_adr_diagram_adr", "adr_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adr_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("adr.row_id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    mermaid: Mapped[str] = mapped_column(Text, nullable=False)

    adr: Mapped[ADRModel] = relationship(back_populates="diagrams")


class ADRSupersedeModel(Base):
    __tablename__ = "adr_supersedes"
    __table_args__ = (
        UniqueConstraint(
            "superseding_id",
            "superseded_id",
            name="uq_adr_supersedes_edge",
        ),
        CheckConstraint(
            "superseding_id <> superseded_id",
            name="ck_adr_supersedes_no_self_loop",
        ),
        Index("ix_adr_supersedes_new", "superseding_id"),
        Index("ix_adr_supersedes_old", "superseded_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    superseding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("adr.row_id", ondelete="CASCADE"), nullable=False
    )
    superseded_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("adr.row_id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ADRTaskModel(Base):
    __tablename__ = "adr_task"
    __table_args__ = (
        UniqueConstraint(
            "adr_row_id",
            "task_id",
            "relation",
            name="uq_adr_task_link",
        ),
        CheckConstraint(
            "relation IN ('implements','invalidates','discovers','relates')",
            name="ck_adr_task_relation",
        ),
        Index("ix_adr_task_task", "task_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adr_row_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("adr.row_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(32), nullable=False)
    relation: Mapped[str] = mapped_column(String(16), nullable=False, default="implements")

    adr: Mapped[ADRModel] = relationship(back_populates="task_links")
