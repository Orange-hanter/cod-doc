"""ADR-007: parse legacy ADRs out of arch/architecture.md and create them in DB.

The legacy format is a markdown section ``## 3. Архитектурные решения (ADR)``
followed by ``### ADR-NNN: <title>`` headings, each with a 5-row table:

| **Статус** | …               |
| **Дата** | YYYY-MM-DD        |
| **Контекст** | free-text     |
| **Решение** | free-text      |
| **Альтернативы** | free-text  |
| **Последствия** | free-text   |

This module extracts those records and feeds them through
``adr_service.create``. Idempotent: re-running won't create duplicates
(skips when an ADR with the same canonical id already exists).
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, Any

from cod_doc.services import adr_service
from cod_doc.services.adr_service import ADRAlreadyExistsError

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


_HEADING_RE = re.compile(r"^###\s+(ADR-\d{3}):\s+(.+?)\s*$", re.MULTILINE)
_TABLE_CELL_RE = re.compile(
    r"^\|\s*\*\*([^*]+)\*\*\s*\|\s*(.+?)\s*\|\s*$",
    re.MULTILINE,
)

# Map legacy table-row labels → adr_service.create keyword.
_LABEL_MAP = {
    "Статус": "status_raw",
    "Status": "status_raw",
    "Дата": "decided_at_raw",
    "Date": "decided_at_raw",
    "Контекст": "context",
    "Context": "context",
    "Решение": "decision",
    "Decision": "decision",
    "Альтернативы": "alternatives",
    "Alternatives": "alternatives",
    "Последствия": "consequences",
    "Consequences": "consequences",
}

# Map status-text variants from legacy docs → canonical enum value.
_STATUS_MAP = {
    "принято": "accepted",
    "accepted": "accepted",
    "✅ принято": "accepted",
    "✅": "accepted",
    "предложено": "proposed",
    "proposed": "proposed",
    "✏️ предложено": "proposed",
    "вытеснено": "superseded",
    "superseded": "superseded",
    "🔁 вытеснено": "superseded",
    "устарело": "deprecated",
    "deprecated": "deprecated",
    "⚠️ устарело": "deprecated",
    "отклонено": "rejected",
    "rejected": "rejected",
    "❌ отклонено": "rejected",
}


def _normalize_status(raw: str) -> str:
    if not raw:
        return "proposed"
    key = raw.strip().lower()
    # strip emoji + leading punctuation that legacy docs prepend.
    key = re.sub(r"[^\wа-яё ]", "", key, flags=re.UNICODE).strip()
    if key in _STATUS_MAP:
        return _STATUS_MAP[key]
    # last-resort fallbacks for partial matches.
    for substring, value in _STATUS_MAP.items():
        if substring in key:
            return value
    return "proposed"


def _parse_decided_at(raw: str) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def parse_adrs_from_markdown(text: str) -> list[dict[str, Any]]:
    """Extract ADR records from a legacy ``arch/architecture.md``-style file.

    Returns a list of dicts with keys ``adr_id``, ``title``, ``status``,
    ``decided_at`` (date | None), ``context``, ``decision``, ``alternatives``,
    ``consequences``. Skips ADRs that lack at least a title.
    """
    headings = list(_HEADING_RE.finditer(text))
    out: list[dict[str, Any]] = []
    for idx, m in enumerate(headings):
        adr_id, title = m.group(1), m.group(2).strip()
        start = m.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        body = text[start:end]
        # Stop at the next ## section if it appears inside this slice.
        stop = re.search(r"^##\s+", body, re.MULTILINE)
        if stop:
            body = body[: stop.start()]

        fields: dict[str, Any] = {
            "adr_id": adr_id,
            "title": title,
            "status": "proposed",
            "decided_at": None,
            "context": None,
            "decision": None,
            "alternatives": None,
            "consequences": None,
        }
        for cell in _TABLE_CELL_RE.finditer(body):
            label = cell.group(1).strip()
            value = cell.group(2).strip()
            key = _LABEL_MAP.get(label)
            if key is None:
                continue
            if key == "status_raw":
                fields["status"] = _normalize_status(value)
            elif key == "decided_at_raw":
                fields["decided_at"] = _parse_decided_at(value)
            else:
                fields[key] = value
        out.append(fields)
    return out


def migrate_from_file(
    session: Session,
    *,
    project_id: int,
    md_path: Path,
    author: str = "human:migration",
) -> dict[str, list[str]]:
    """Parse ``md_path`` and create any not-yet-existing ADRs in ``project_id``.

    Returns ``{created: [adr_id, ...], skipped: [adr_id, ...]}``.
    """
    text = md_path.read_text(encoding="utf-8")
    records = parse_adrs_from_markdown(text)
    created: list[str] = []
    skipped: list[str] = []

    for r in records:
        try:
            adr_service.create(
                session,
                project_id=project_id,
                title=r["title"],
                status=r["status"],
                decided_at=r["decided_at"],
                context=r["context"],
                decision=r["decision"],
                alternatives=r["alternatives"],
                consequences=r["consequences"],
                adr_id=r["adr_id"],
                author=author,
            )
            created.append(r["adr_id"])
        except ADRAlreadyExistsError:
            skipped.append(r["adr_id"])

    return {"created": created, "skipped": skipped}
