"""Pure-Python text helpers shared by services + migrations.

Lives at the domain layer (no SQLAlchemy / FastAPI imports) so backfill
migrations can call it without dragging app code into Alembic.
"""

from __future__ import annotations

import re

_TITLE_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_TITLE_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace.

    Used by ``task_service.find_duplicate_by_title`` and the
    ``task.normalized_title`` index column to compare task titles ignoring
    case, punctuation and minor whitespace differences.
    """
    s = _TITLE_PUNCT_RE.sub(" ", title.lower())
    return _TITLE_WS_RE.sub(" ", s).strip()
