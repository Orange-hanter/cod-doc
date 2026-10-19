"""0053: доступ к умному remote — ``actor``, ``api_token``, ``project_member``.

RFC 29 §3.8 — CLO-034. Номер 0052 из RFC занят ``0052_sync_conflict_opened_hlc``
(CLO-045), отсюда 0053 (CLO-049).

Таблицы нужны только хранилищу доступа сервера (``services/access_service``);
в БД проекта они остаются пустыми. Все три — ``local`` в ``sync_registry``, эпоху
не открывают (``infra/sync_epochs.py``): authored-колонок миграция не трогает.

Только ``CREATE TABLE``; downgrade — ``DROP TABLE`` (участники и токены теряются:
их нет в схеме 0052).

Revision ID: 0053_sync_access
Revises: 0052_sync_conflict_opened_hlc
Create Date: 2026-10-19
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0053_sync_access"
down_revision: str | None = "0052_sync_conflict_opened_hlc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTOR_FK = "actor.uid"


def upgrade() -> None:
    op.create_table(
        "actor",
        sa.Column("uid", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("handle", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "owner_uid", sa.Text(), sa.ForeignKey(_ACTOR_FK, ondelete="CASCADE"), nullable=True
        ),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("kind IN ('human','agent')", name="ck_actor_kind"),
        sa.CheckConstraint(
            "(kind = 'agent') = (owner_uid IS NOT NULL)", name="ck_actor_agent_has_owner"
        ),
    )
    op.create_table(
        "api_token",
        sa.Column("uid", sa.Text(), primary_key=True),
        sa.Column(
            "actor_uid", sa.Text(), sa.ForeignKey(_ACTOR_FK, ondelete="CASCADE"), nullable=False
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_token_actor", "api_token", ["actor_uid"])
    op.create_table(
        "project_member",
        sa.Column("project_uid", sa.Text(), primary_key=True),
        sa.Column(
            "actor_uid",
            sa.Text(),
            sa.ForeignKey(_ACTOR_FK, ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.CheckConstraint("role IN ('reader','writer','admin')", name="ck_project_member_role"),
    )


def downgrade() -> None:
    op.drop_table("project_member")
    op.drop_index("ix_api_token_actor", table_name="api_token")
    op.drop_table("api_token")
    op.drop_table("actor")
