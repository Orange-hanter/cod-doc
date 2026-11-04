"""0056: OIDC-логин веба — ``actor.oidc_sub`` + ``oidc_meta``.

Веб-клиент входит через внешний OIDC-провайдер (Pocket ID): ``actor.oidc_sub``
— стабильный ``sub`` из claims, по которому ``access_service.authenticate_by_sub``
находит актёра. UNIQUE-индекс разрешает много NULL: не у всех актёров есть
привязка (``ALTER TABLE ADD COLUMN`` не умеет UNIQUE, поэтому индекс отдельным
оператором; SQLite допускает много NULL в UNIQUE-индексе).

``oidc_meta`` — локальные настройки веб-логина сервера (ключ подписи сессии):
таблица есть в общей схеме ради одной цепочки миграций, живёт только в
хранилище доступа (``access.db``), в реестре — ``local``, эпоху не открывает.

Downgrade симметричен: ``DROP TABLE`` + ``DROP COLUMN`` (привязки и ключ
теряются — их нет в схеме 0055).

Revision ID: 0056_oidc_web_login
Revises: 0055_task_estimate
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0056_oidc_web_login"
down_revision: str | None = "0055_task_estimate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE actor ADD COLUMN oidc_sub TEXT")
    op.execute("CREATE UNIQUE INDEX uq_actor_oidc_sub ON actor(oidc_sub)")
    op.execute("CREATE TABLE oidc_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def downgrade() -> None:
    op.execute("DROP TABLE oidc_meta")
    op.execute("DROP INDEX uq_actor_oidc_sub")
    op.execute("ALTER TABLE actor DROP COLUMN oidc_sub")
