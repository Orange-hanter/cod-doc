"""DeclarativeBase + UTC-now helper shared by every model."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SyncUidMixin:
    """Глобальная идентичность строки authored-таблицы (RFC 29 §3.3, CLO-002).

    Колонку и ``UNIQUE``-индекс ``uq_<t>_uid`` создаёт миграция 0049; индекс к
    модели цепляет ``models/__init__.py`` по всем наследникам. Nullable: NOT
    NULL обеспечивает захват (CLO-006), а не ограничение, — ``ADD COLUMN NOT
    NULL`` без ``DEFAULT`` на SQLite невозможен.
    """

    uid: Mapped[str | None] = mapped_column(Text, nullable=True)


def _utcnow() -> datetime:
    return datetime.now(UTC)
