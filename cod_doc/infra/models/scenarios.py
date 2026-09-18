"""Scenario + ScenarioStep + ScenarioLink — the authoring half of [RFC 24 §9].

These three tables are the "normalized scenario index" RFC 24 §12 defers to a
later phase. They hold intentions only; the evidence half (coverage verdicts
derived from producer output) lands in a separate ``scenario_assessment`` table
in STR-002 and joins back on ``scenario.row_id``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import (
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


class ScenarioModel(Base):
    __tablename__ = "scenario"
    __table_args__ = (
        UniqueConstraint("project_id", "scenario_id", name="uq_scenario_project_sid"),
        Index("ix_scenario_project_group", "project_id", "group_key", "position"),
        Index("ix_scenario_document_id", "document_id"),
        Index("ix_scenario_project_kind", "project_id", "kind"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    scenario_id: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    group_key: Mapped[str] = mapped_column(String(64), nullable=False)

    document_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="SET NULL"), nullable=True
    )
    doc_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    section_anchor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    doc_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Reserved: the module table is unused today, the document anchor above is
    # what actually groups scenarios.
    module_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("module.row_id", ondelete="SET NULL"), nullable=True
    )
    subject_ref: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    provenance: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")

    preconditions: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    author: Mapped[str] = mapped_column(String(64), nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    steps: Mapped[list[ScenarioStepModel]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
        order_by="ScenarioStepModel.position",
    )
    links: Mapped[list[ScenarioLinkModel]] = relationship(
        back_populates="scenario",
        cascade="all, delete-orphan",
    )


class ScenarioStepModel(Base):
    __tablename__ = "scenario_step"
    __table_args__ = (
        UniqueConstraint("scenario_row_id", "position", name="uq_scenario_step_position"),
        Index("ix_scenario_step_scenario", "scenario_row_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_row_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scenario.row_id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    scenario: Mapped[ScenarioModel] = relationship(back_populates="steps")


class ScenarioLinkModel(Base):
    __tablename__ = "scenario_link"
    __table_args__ = (
        UniqueConstraint(
            "scenario_row_id",
            "to_kind",
            "to_ref",
            "relation",
            name="uq_scenario_link_edge",
        ),
        Index("ix_scenario_link_scenario", "scenario_row_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scenario_row_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scenario.row_id", ondelete="CASCADE"), nullable=False
    )
    to_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)

    scenario: Mapped[ScenarioModel] = relationship(back_populates="links")
