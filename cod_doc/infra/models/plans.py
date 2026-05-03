"""Plan + PlanSection + Task + Dependency + AffectedFile."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import (
    CheckConstraint,
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


class PlanModel(Base):
    __tablename__ = "plan"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    principle: Mapped[str | None] = mapped_column(String(32))
    module_id: Mapped[str | None] = mapped_column(String(64))
    parent_doc_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="SET NULL")
    )
    completed_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="SET NULL")
    )
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    sections: Mapped[list[PlanSectionModel]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanSectionModel.position",
    )
    tasks: Mapped[list[TaskModel]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
    )


class PlanSectionModel(Base):
    __tablename__ = "plan_section"
    __table_args__ = (UniqueConstraint("plan_id", "letter", name="uq_plan_section_plan_letter"),)

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.row_id", ondelete="CASCADE"), nullable=False
    )
    letter: Mapped[str] = mapped_column(String(4), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    doc_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="SET NULL")
    )

    plan: Mapped[PlanModel] = relationship(back_populates="sections")
    tasks: Mapped[list[TaskModel]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
    )


class TaskModel(Base):
    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_status", "status", "priority"),
        Index("ix_task_plan", "plan_id", "section_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    plan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan.row_id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("plan_section.row_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    acceptance: Mapped[str | None] = mapped_column(Text)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_commit: Mapped[str | None] = mapped_column(String(64))
    blocked_reason: Mapped[str | None] = mapped_column(Text)

    plan: Mapped[PlanModel] = relationship(back_populates="tasks")
    section: Mapped[PlanSectionModel] = relationship(back_populates="tasks")
    affected_files: Mapped[list[AffectedFileModel]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )
    outgoing_deps: Mapped[list[DependencyModel]] = relationship(
        back_populates="from_task",
        cascade="all, delete-orphan",
        foreign_keys="DependencyModel.from_task_id",
    )


class DependencyModel(Base):
    __tablename__ = "dependency"
    __table_args__ = (
        UniqueConstraint("from_task_id", "to_task_id", "kind", name="uq_dependency_edge"),
        CheckConstraint("from_task_id <> to_task_id", name="ck_dependency_no_self_loop"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task.row_id", ondelete="CASCADE"), nullable=False
    )
    to_task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task.row_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="blocks")
    note: Mapped[str | None] = mapped_column(Text)

    from_task: Mapped[TaskModel] = relationship(
        back_populates="outgoing_deps",
        foreign_keys=[from_task_id],
    )
    to_task: Mapped[TaskModel] = relationship(foreign_keys=[to_task_id])


class AffectedFileModel(Base):
    __tablename__ = "affected_file"
    __table_args__ = (
        UniqueConstraint("task_id", "path", name="uq_affected_file_task_path"),
        Index("ix_affected_path", "path"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("task.row_id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="source")

    task: Mapped[TaskModel] = relationship(back_populates="affected_files")
