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
    # OBI-020: code-ref fields. Populated when kind == LinkKind.CODE.
    target_file_path: str | None = None
    target_symbol: str | None = None  # '#symbol_name' fragment


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


@dataclass(slots=True)
class IncomingLink:
    """COD-078: a link pointing AT the inspected document.

    Carries enough source-doc context (doc_key + title) and source-section
    context (heading + anchor) for the doc-show "Incoming" panel to render
    without the web layer reaching into the infra repositories.
    """

    source_doc_key: str
    source_doc_title: str
    section_heading: str
    section_anchor: str
    label: str | None = None
