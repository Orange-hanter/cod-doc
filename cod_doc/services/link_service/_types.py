"""Parsed-link DTO + verify/rename reports + error type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cod_doc.domain.entities import LinkKind


class LinkNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class ParsedLink:
    raw: str
    kind: LinkKind
    target_doc_key: str | None = None
    target_task_id: str | None = None
    target_story_id: str | None = None
    target_label: str | None = None  # wiki-link label (pre-resolution)
    anchor: str | None = None
    start: int = 0  # position in the (code-block-stripped) body


@dataclass(slots=True)
class VerifyReport:
    section_id: int
    ok: int
    broken: int
    skipped: int


@dataclass(slots=True)
class RenameCascadeReport:
    old_doc_key: str
    new_doc_key: str
    updated_links: int
    rewritten_sections: int
