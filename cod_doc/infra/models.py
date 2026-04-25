"""SQLAlchemy ORM models — infra-side mapping of domain entities.

Schema mirrors docs/system/DATA_MODEL.md §3.1-3.13 + §4.3a (document_body view).
Sections 3.14 / 3.15 (embedding, proposal) — later migrations.

Convention: timestamp columns (`created`, `last_updated`, `at`) are NOT NULL
without a server_default — they are always populated through the ORM
(Python-side default `_utcnow`). Raw SQL inserts must specify them explicitly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectModel(Base):
    __tablename__ = "project"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    documents: Mapped[list[DocumentModel]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class DocumentModel(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("project_id", "doc_key", name="uq_document_project_key"),
        Index("ix_document_type", "type", "status"),
        Index("ix_document_sensitivity", "sensitivity"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    doc_key: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_of_truth: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False, default="internal")
    owner: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    preamble: Mapped[str] = mapped_column(Text, nullable=False, default="")
    frontmatter_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    projection_hash: Mapped[str | None] = mapped_column(String(64))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_reviewed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ProjectModel] = relationship(back_populates="documents")
    sections: Mapped[list[SectionModel]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="SectionModel.position",
    )


class SectionModel(Base):
    __tablename__ = "section"
    __table_args__ = (
        UniqueConstraint("document_id", "anchor", name="uq_section_document_anchor"),
        Index("ix_section_position", "document_id", "position"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="CASCADE"), nullable=False
    )
    anchor: Mapped[str] = mapped_column(String(255), nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    document: Mapped[DocumentModel] = relationship(back_populates="sections")
    outgoing_links: Mapped[list[LinkModel]] = relationship(
        back_populates="from_section",
        cascade="all, delete-orphan",
    )


class LinkModel(Base):
    __tablename__ = "link"
    __table_args__ = (
        Index("ix_link_target_doc", "to_doc_key"),
        Index("ix_link_target_task", "to_task_id"),
        Index("ix_link_unresolved", "resolved"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    from_section_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("section.row_id", ondelete="CASCADE"), nullable=False
    )
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_doc_key: Mapped[str | None] = mapped_column(String(255))
    to_task_id: Mapped[str | None] = mapped_column(String(32))
    to_story_id: Mapped[str | None] = mapped_column(String(32))
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broken_reason: Mapped[str | None] = mapped_column(Text)

    from_section: Mapped[SectionModel] = relationship(back_populates="outgoing_links")


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
    __table_args__ = (
        UniqueConstraint("plan_id", "letter", name="uq_plan_section_plan_letter"),
    )

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


class UserStoryModel(Base):
    __tablename__ = "user_story"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    story_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    persona: Mapped[str] = mapped_column(String(128), nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    acceptance: Mapped[list[StoryAcceptanceModel]] = relationship(
        back_populates="story",
        cascade="all, delete-orphan",
        order_by="StoryAcceptanceModel.position",
    )
    links: Mapped[list[StoryLinkModel]] = relationship(
        back_populates="story",
        cascade="all, delete-orphan",
    )


class StoryAcceptanceModel(Base):
    __tablename__ = "story_acceptance"
    __table_args__ = (
        UniqueConstraint("story_id", "position", name="uq_story_acceptance_position"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_story.row_id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    criterion: Mapped[str] = mapped_column(Text, nullable=False)
    met: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    story: Mapped[UserStoryModel] = relationship(back_populates="acceptance")


class StoryLinkModel(Base):
    __tablename__ = "story_link"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_story.row_id", ondelete="CASCADE"), nullable=False
    )
    to_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)

    story: Mapped[UserStoryModel] = relationship(back_populates="links")


class ModuleModel(Base):
    __tablename__ = "module"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    spec_doc_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="SET NULL")
    )
    plan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("plan.row_id", ondelete="SET NULL")
    )

    code_paths: Mapped[list[ModuleCodeModel]] = relationship(
        back_populates="module",
        cascade="all, delete-orphan",
    )
    outgoing_deps: Mapped[list[ModuleDependencyModel]] = relationship(
        back_populates="from_module_rel",
        cascade="all, delete-orphan",
        foreign_keys="ModuleDependencyModel.from_module",
    )


class ModuleDependencyModel(Base):
    __tablename__ = "module_dependency"
    __table_args__ = (
        UniqueConstraint("from_module", "to_module", name="uq_module_dependency_edge"),
        CheckConstraint("from_module <> to_module", name="ck_module_dependency_no_self_loop"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_module: Mapped[int] = mapped_column(
        Integer, ForeignKey("module.row_id", ondelete="CASCADE"), nullable=False
    )
    to_module: Mapped[int] = mapped_column(
        Integer, ForeignKey("module.row_id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)

    from_module_rel: Mapped[ModuleModel] = relationship(
        back_populates="outgoing_deps",
        foreign_keys=[from_module],
    )
    to_module_rel: Mapped[ModuleModel] = relationship(foreign_keys=[to_module])


class ModuleCodeModel(Base):
    __tablename__ = "module_code"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("module.row_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)

    module: Mapped[ModuleModel] = relationship(back_populates="code_paths")


class RevisionModel(Base):
    __tablename__ = "revision"
    __table_args__ = (
        Index("ix_revision_entity", "entity_kind", "entity_id", "at"),
        Index("ix_revision_parent", "parent_revision_id"),
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
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    diff: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(String(64))


class AuditLogModel(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_action", "action", "at"),
        Index("ix_audit_actor", "actor", "at"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    surface: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    result: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class TagModel(Base):
    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_tag_project_name"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class DocumentTagModel(Base):
    __tablename__ = "document_tag"

    document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("document.row_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tag.row_id", ondelete="CASCADE"),
        primary_key=True,
    )


class TaskTagModel(Base):
    __tablename__ = "task_tag"

    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("task.row_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tag.row_id", ondelete="CASCADE"),
        primary_key=True,
    )


class StoryTagModel(Base):
    __tablename__ = "story_tag"

    story_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user_story.row_id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tag.row_id", ondelete="CASCADE"),
        primary_key=True,
    )
