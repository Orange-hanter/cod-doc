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


class RankMixin:
    """Синхронизируемый порядок строки внутри родителя (RFC 29 §3.2, Q-009, CLO-003).

    Дробный ранг из ``cod_doc.infra.rank``; колонку создаёт миграция 0050.
    Порядок чтения — ``(rank, uid, row_id)`` (``infra/ordering.py``), а целый
    ``position`` рядом — local-производная, пересчитываемая из ранга. Nullable
    по той же причине, что ``uid``: ранг ставит ``ordering``, а не ограничение.
    """

    rank: Mapped[str | None] = mapped_column(Text, nullable=True)


def _utcnow() -> datetime:
    return datetime.now(UTC)
