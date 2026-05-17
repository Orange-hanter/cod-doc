"""Document + Section + Link — the documentation tree + outgoing references."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _utcnow

if TYPE_CHECKING:
    from .project import ProjectModel


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
    # PCA-928: sha256 of the first 4 KB of the source file at last import.
    # Used by scan_folder() to detect "changed" status without re-parsing.
    content_sha256_head: Mapped[str | None] = mapped_column(String(64))
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
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
        Index("ix_link_target_adr", "to_adr_id"),
        Index("ix_link_target_file", "to_file_path"),
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
    to_adr_id: Mapped[str | None] = mapped_column(String(16))
    # OBI-020: code-ref fields (kind == 'code')
    to_file_path: Mapped[str | None] = mapped_column(String(512))
    to_symbol: Mapped[str | None] = mapped_column(String(128))
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broken_reason: Mapped[str | None] = mapped_column(Text)

    from_section: Mapped[SectionModel] = relationship(back_populates="outgoing_links")
