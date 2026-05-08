"""LinkSuggestion model — PCA-422: semantic backfill suggestions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow

_SUGGESTION_STATES = ("pending", "accepted", "rejected")


class LinkSuggestionModel(Base):
    """Semantic-similarity-based link suggestion between two sections.

    Populated by ``link_service.semantic.suggest_for_section()`` (PCA-422).
    Not written to the ``link`` table directly — user must Accept to create
    the canonical link edge.

    Composite unique: (from_section_id, to_doc_key, to_section_id) so that
    re-running the suggester is idempotent (upsert on score/evidence).
    """

    __tablename__ = "link_suggestion"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_section_id: Mapped[int] = mapped_column(Integer, nullable=False)
    to_doc_key: Mapped[str] = mapped_column(String(512), nullable=False)
    to_section_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        Index("ix_lsugg_from_section", "from_section_id"),
        Index("ix_lsugg_state", "state"),
        Index(
            "uq_lsugg_triple",
            "from_section_id", "to_doc_key", "to_section_id",
            unique=True,
        ),
    )
