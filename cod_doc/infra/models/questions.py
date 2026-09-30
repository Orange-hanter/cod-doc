"""OpenQuestion + QuestionOption + QuestionLink — открытые вопросы проекта.

Вопросы живут только в БД: ни одна из этих таблиц не проецируется в markdown.
Ссылки вопроса устроены по образцу ``scenario_link`` (``to_kind`` / ``to_ref`` /
``relation``), но несут ещё результат проверки — ``resolved`` /
``broken_reason`` / ``last_checked``, — потому что вопрос ссылается и на код,
а код переезжает.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import (
    Boolean,
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


class OpenQuestionModel(Base):
    __tablename__ = "open_question"
    __table_args__ = (
        UniqueConstraint("project_id", "question_id", name="uq_open_question_project_qid"),
        CheckConstraint(
            "status IN ('open','resolved','dropped')",
            name="ck_open_question_status",
        ),
        Index("ix_open_question_project_status", "project_id", "status"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_adr: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_doc_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    author: Mapped[str] = mapped_column(String(64), nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    options: Mapped[list[QuestionOptionModel]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionOptionModel.position",
    )
    links: Mapped[list[QuestionLinkModel]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
    )


class QuestionOptionModel(Base):
    __tablename__ = "open_question_option"
    __table_args__ = (
        UniqueConstraint("question_row_id", "position", name="uq_open_question_option_position"),
        Index("ix_open_question_option_question", "question_row_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_row_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("open_question.row_id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    chosen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    question: Mapped[OpenQuestionModel] = relationship(back_populates="options")


class QuestionLinkModel(Base):
    __tablename__ = "open_question_link"
    __table_args__ = (
        UniqueConstraint(
            "question_row_id",
            "to_kind",
            "to_ref",
            "relation",
            name="uq_open_question_link_edge",
        ),
        Index("ix_open_question_link_question", "question_row_id"),
        Index("ix_open_question_link_target", "to_kind", "to_ref"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_row_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("open_question.row_id", ondelete="CASCADE"), nullable=False
    )
    to_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    broken_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    question: Mapped[OpenQuestionModel] = relationship(back_populates="links")
