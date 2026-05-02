"""UserStory + StoryAcceptance + StoryLink."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _utcnow


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
