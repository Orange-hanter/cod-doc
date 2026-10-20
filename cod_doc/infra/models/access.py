"""Доступ к умному remote (RFC 29 §3.8, миграция 0053, CLO-034).

``actor`` — сущность **сервера**, а не проекта (поправка к ARCHITECTURE §12.2):
один человек с одним токеном видит все проекты, в которых он участник.
Поэтому строки живут в отдельном хранилище доступа сервера
(``services/access_service``), а не в БД проекта; таблицы есть в общей схеме
ради одной цепочки миграций. Все три — ``local`` в ``sync_registry``: не
уезжают никогда, токен чужой реплике незачем.

``project_member.project_uid`` — ``project.uid`` проекта, без FK: проект живёт
в своей БД, а членство — в хранилище доступа.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow

_ACTOR_FK = "actor.uid"


class ActorModel(Base):
    """Человек или его агент. ``handle`` агента — ``<владелец>/<имя>``."""

    __tablename__ = "actor"
    __table_args__ = (
        CheckConstraint("kind IN ('human','agent')", name="ck_actor_kind"),
        CheckConstraint(
            "(kind = 'agent') = (owner_uid IS NOT NULL)", name="ck_actor_agent_has_owner"
        ),
    )

    uid: Mapped[str] = mapped_column(Text, primary_key=True)  # uuid7
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    handle: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner_uid: Mapped[str | None] = mapped_column(
        Text, ForeignKey(_ACTOR_FK, ondelete="CASCADE"), nullable=True
    )
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class ApiTokenModel(Base):
    """Bearer-токен: хранится только ``sha256``, сам токен показан один раз."""

    __tablename__ = "api_token"
    __table_args__ = (Index("ix_api_token_actor", "actor_uid"),)

    uid: Mapped[str] = mapped_column(Text, primary_key=True)  # uuid7
    actor_uid: Mapped[str] = mapped_column(
        Text, ForeignKey(_ACTOR_FK, ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectMemberModel(Base):
    """Роль actor'а в проекте: ``reader`` | ``writer`` | ``admin``."""

    __tablename__ = "project_member"
    __table_args__ = (
        CheckConstraint("role IN ('reader','writer','admin')", name="ck_project_member_role"),
    )

    project_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    actor_uid: Mapped[str] = mapped_column(
        Text, ForeignKey(_ACTOR_FK, ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
