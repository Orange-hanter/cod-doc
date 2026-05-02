"""ProjectModel — root entity that owns documents/plans/stories."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _utcnow

if TYPE_CHECKING:
    from .documents import DocumentModel


class ProjectModel(Base):
    __tablename__ = "project"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    documents: Mapped[list[DocumentModel]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
