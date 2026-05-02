"""Tag + association tables for documents/tasks/stories."""

from __future__ import annotations

from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TagModel(Base):
    __tablename__ = "tag"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_tag_project_name"),)

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
