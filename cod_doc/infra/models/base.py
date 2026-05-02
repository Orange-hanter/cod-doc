"""DeclarativeBase + UTC-now helper shared by every model."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)
