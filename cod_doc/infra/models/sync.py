"""Служебные таблицы синхронизации (RFC 29 §10.1, миграция 0049, CLO-002).

Все — ``local`` в ``sync_registry``: это состояние реплики (кто она, что
записала, что знает о remote, какие номера арендовала, какие имена
переименованы), а не данные проекта.
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
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow

_PROJECT_FK = "project.row_id"


class ReplicaModel(Base):
    __tablename__ = "replica"
    __table_args__ = (
        Index(
            "uq_replica_self",
            "project_id",
            unique=True,
            sqlite_where=text("is_self = 1"),
            postgresql_where=text("is_self"),
        ),
    )

    uid: Mapped[str] = mapped_column(Text, primary_key=True)  # uuid7
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(_PROJECT_FK, ondelete="CASCADE"), nullable=False
    )
    is_self: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class SyncOpModel(Base):
    __tablename__ = "sync_op"
    __table_args__ = (
        CheckConstraint("kind IN ('upsert','delete')", name="ck_sync_op_kind"),
        Index(
            "ix_sync_op_pending",
            "project_id",
            sqlite_where=text("changeset IS NULL"),
            postgresql_where=text("changeset IS NULL"),
        ),
        Index("ix_sync_op_entity", "entity_kind", "entity_uid"),
    )

    op_id: Mapped[str] = mapped_column(Text, primary_key=True)  # ULID
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(_PROJECT_FK, ondelete="CASCADE"), nullable=False
    )
    replica_uid: Mapped[str] = mapped_column(Text, nullable=False)
    hlc: Mapped[str] = mapped_column(Text, nullable=False)
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_uid: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    fields: Mapped[str] = mapped_column(Text, nullable=False)
    base: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(Text, nullable=False)
    changeset: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChangesetModel(Base):
    __tablename__ = "changeset"

    sha: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(_PROJECT_FK, ondelete="CASCADE"), nullable=False
    )
    parents: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-массив sha
    replica_uid: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    epoch: Mapped[str] = mapped_column(Text, nullable=False)
    hlc_max: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncRefModel(Base):
    __tablename__ = "sync_ref"

    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(_PROJECT_FK, ondelete="CASCADE"), primary_key=True
    )
    remote: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, primary_key=True)
    sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncLeaseModel(Base):
    __tablename__ = "sync_lease"

    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(_PROJECT_FK, ondelete="CASCADE"), primary_key=True
    )
    prefix: Mapped[str] = mapped_column(Text, primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False)
    end_number: Mapped[int] = mapped_column(Integer, nullable=False)  # включительно
    leased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class EntityAliasModel(Base):
    __tablename__ = "entity_alias"
    __table_args__ = (
        CheckConstraint("reason IN ('pinned','merged')", name="ck_entity_alias_reason"),
    )

    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(_PROJECT_FK, ondelete="CASCADE"), primary_key=True
    )
    entity_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    uid: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
