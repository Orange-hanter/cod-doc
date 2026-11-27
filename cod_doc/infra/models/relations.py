"""Relation + RelationType — граф связей (RFC 39 §3.3).

Конец ребра — ``uid`` строки-цели (не ``row_id`` и не публичный ключ), FK на
концах нет. ``to_uid`` NULL: конец ``code:``/``url:`` или ссылка вперёд
(литерал в ``to_key``). ``resolved``/``broken_reason``/``last_checked`` —
результат проверки на этой реплике, в sync они local.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

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
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, SyncUidMixin, _utcnow


class RelationModel(SyncUidMixin, Base):
    __tablename__ = "relation"
    __table_args__ = (
        CheckConstraint("to_uid IS NOT NULL OR to_key IS NOT NULL", name="ck_relation_has_target"),
        UniqueConstraint(
            "project_id", "from_uid", "type", "to_kind", "to_uid", "to_key", name="uq_relation_edge"
        ),
        Index(
            "uq_relation_edge_literal",
            "project_id",
            "from_uid",
            "type",
            "to_kind",
            "to_key",
            unique=True,
            sqlite_where=text("to_uid IS NULL"),
            postgresql_where=text("to_uid IS NULL"),
        ),
        Index("ix_relation_to", "project_id", "to_uid"),
        Index("ix_relation_key", "project_id", "to_kind", "to_key"),
        Index("ix_relation_from", "project_id", "from_uid"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    from_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    from_uid: Mapped[str] = mapped_column(Text, nullable=False)
    #: Каноническое имя типа: не алиас и не обратное имя.
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    to_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_uid: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    broken_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class RelationTypeModel(SyncUidMixin, Base):
    """Проектный тип отношения; встроенные живут в ``domain/relation_types.py``."""

    __tablename__ = "relation_type"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_relation_type_name"),)

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(48), nullable=False)
    inverse: Mapped[str] = mapped_column(String(48), nullable=False)
    from_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    to_kinds: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    symmetric: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    acyclic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    allow_forward: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deprecated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
