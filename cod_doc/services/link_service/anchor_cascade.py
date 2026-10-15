"""anchor_cascade — rewrite links to a section after its anchor changes (DEBT-008).

Counterpart of ``rename_cascade`` one level down: there the document key
moves, here one section's anchor does. Three link forms carry an anchor:

- canonical ``[[doc:KEY#OLD]]``;
- markdown ``[label](path.md#OLD)``;
- same-document ``[label](#OLD)``.

The anchor is not stored on ``link`` rows — only in ``raw`` — so the cascade
works on section bodies: candidate sections are those holding a SECTION link
already resolved to the document (``link.to_doc_key``) plus the document's own
sections; in each, every parsed SECTION link whose anchor is OLD and whose
target resolves to the document — by the resolver's own candidate rules — is
rewritten. Bodies go through ``doc_service.patch_section``: one SECTION
revision per changed section, and its link auto-sync re-resolves the rows
against the already-renamed section.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import select

from cod_doc.domain.entities import LinkKind
from cod_doc.infra.models import DocumentModel, LinkModel, SectionModel

from .parser import _MD_LINK_RE, parse
from .resolver import _lookup_doc_id, _target_candidates

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ._types import ParsedLink


class AnchorCascadeReport(NamedTuple):
    """``rewritten`` — ``doc_key#anchor`` of every section whose body changed."""

    rewritten: list[str]
    links: int


def _targets_doc(
    session: Session, project_id: int, parsed: ParsedLink, host_key: str, doc_key: str
) -> bool:
    """Same matching as ``resolver._resolve_section_anchor``: first existing candidate wins."""
    key = parsed.target_doc_key or host_key
    for cand in _target_candidates(key, host_key):
        if _lookup_doc_id(session, project_id, cand) is not None:
            return cand == doc_key
    return False


def _with_anchor(raw: str, new_anchor: str) -> str:
    """Replace the fragment of one link's raw text, keeping everything else."""
    if raw.startswith("[[doc:"):
        key = raw[len("[[doc:") : -2].partition("#")[0]
        return f"[[doc:{key}#{new_anchor}]]"
    m = _MD_LINK_RE.fullmatch(raw)
    if m is None:
        return raw
    base = m.group(2).strip().partition("#")[0]
    return f"[{m.group(1)}]({base}#{new_anchor})"


def anchor_cascade(
    session: Session,
    *,
    project_id: int,
    document_id: int,
    old_anchor: str,
    new_anchor: str,
    author: str,
    reason: str | None = None,
) -> AnchorCascadeReport:
    """Rewrite every link to ``document#old_anchor`` so it points at ``#new_anchor``.

    Pre-condition: the section row already carries ``new_anchor``.
    """
    from cod_doc.services import doc_service as docs

    doc = session.get(DocumentModel, document_id)
    assert doc is not None
    section_ids = set(
        session.execute(
            select(LinkModel.from_section_id).where(
                LinkModel.project_id == project_id,
                LinkModel.kind == LinkKind.SECTION.value,
                LinkModel.to_doc_key == doc.doc_key,
            )
        ).scalars()
    )
    section_ids |= set(
        session.execute(
            select(SectionModel.row_id).where(SectionModel.document_id == document_id)
        ).scalars()
    )

    rewritten: list[str] = []
    links = 0
    for section_id in sorted(section_ids):
        sec = session.get(SectionModel, section_id)
        if sec is None:
            continue
        host = session.get(DocumentModel, sec.document_id)
        if host is None:
            continue
        new_body = sec.body
        for parsed in parse(sec.body):
            if parsed.kind is not LinkKind.SECTION or parsed.anchor != old_anchor:
                continue
            if not _targets_doc(session, project_id, parsed, host.doc_key, doc.doc_key):
                continue
            replacement = _with_anchor(parsed.raw, new_anchor)
            if replacement != parsed.raw and parsed.raw in new_body:
                links += new_body.count(parsed.raw)
                new_body = new_body.replace(parsed.raw, replacement)
        if new_body == sec.body:
            continue
        docs.patch_section(
            session,
            project_id=project_id,
            document_id=sec.document_id,
            anchor=sec.anchor,
            new_body=new_body,
            author=author,
            reason=reason or f"anchor_cascade {doc.doc_key}#{old_anchor} → #{new_anchor}",
        )
        rewritten.append(f"{host.doc_key}#{sec.anchor}")
    return AnchorCascadeReport(rewritten, links)
