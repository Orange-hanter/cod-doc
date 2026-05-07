"""Sync / resolve / verify pipeline (DB-bound).

Replaces stored `link` rows from a section's current parse output, then
looks each link's target up in the DB and stamps `to_*` / `resolved` /
`broken_reason` accordingly.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import Link, LinkKind
from cod_doc.infra.models import (
    DocumentModel,
    LinkModel,
    SectionModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.repositories import LinkRepository

from ._section_helpers import _link_or_raise, _project_id_for_section, _section_or_raise
from ._types import IncomingLink, ParsedLink, VerifyReport
from .parser import parse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_MD_LABEL_RE = re.compile(r"\[([^\]]+)\]")


def _extract_label(raw: str) -> str | None:
    """Extract `[label](...)` text from a parsed link's raw string.

    Returns the first bracketed-label group, or None if the raw form has
    no brackets (e.g. bare URL or `[[doc:KEY]]` canonical refs — those
    shouldn't reach IncomingLink rendering anyway).
    """
    m = _MD_LABEL_RE.match(raw)
    return m.group(1) if m else None


def _resolve_canonical(
    session: Session, project_id: int, doc_key: str | None
) -> tuple[bool, str | None, str | None]:
    """Return (resolved, to_doc_key_to_stamp, broken_reason)."""
    if not doc_key:
        return False, None, "missing doc_key"
    stmt = select(DocumentModel.row_id).where(
        DocumentModel.project_id == project_id,
        DocumentModel.doc_key == doc_key,
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        return False, None, f"document not found: {doc_key}"
    return True, doc_key, None


def _resolve_section_anchor(
    session: Session, project_id: int, doc_key: str | None, anchor: str | None
) -> tuple[bool, str | None, str | None]:
    if not doc_key:
        return False, None, "missing doc_key"
    doc_row = session.execute(
        select(DocumentModel.row_id).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()
    if doc_row is None:
        return False, None, f"document not found: {doc_key}"
    if not anchor:
        return False, doc_key, "missing anchor"
    sec = session.execute(
        select(SectionModel.row_id).where(
            SectionModel.document_id == doc_row,
            SectionModel.anchor == anchor,
        )
    ).scalar_one_or_none()
    if sec is None:
        # Stamp doc_key (we know the target doc) but flag broken.
        return False, doc_key, f"anchor not found: {anchor}"
    return True, doc_key, None


def _resolve_task(
    session: Session, project_id: int, task_id: str | None
) -> tuple[bool, str | None, str | None]:
    if not task_id:
        return False, None, "missing task_id"
    stmt = select(TaskModel.row_id).where(
        TaskModel.project_id == project_id,
        TaskModel.task_id == task_id,
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        return False, None, f"task not found: {task_id}"
    return True, task_id, None


def _resolve_story(
    session: Session, project_id: int, story_id: str | None
) -> tuple[bool, str | None, str | None]:
    if not story_id:
        return False, None, "missing story_id"
    stmt = select(UserStoryModel.row_id).where(
        UserStoryModel.project_id == project_id,
        UserStoryModel.story_id == story_id,
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        return False, None, f"story not found: {story_id}"
    return True, story_id, None


def _resolve_wiki(
    session: Session, project_id: int, label: str | None
) -> tuple[bool, str | None, str | None]:
    """Exact match by `title` or `doc_key`. Fuzzy fallback is deferred."""
    if not label:
        return False, None, "missing label"
    stmt = select(DocumentModel.doc_key).where(
        DocumentModel.project_id == project_id,
        (DocumentModel.title == label) | (DocumentModel.doc_key == label),
    )
    doc_key = session.execute(stmt).scalars().first()
    if doc_key is None:
        return False, None, f"wiki target not found: {label}"
    return True, doc_key, None


def _reparse_link(model: LinkModel) -> ParsedLink:
    """Re-derive a ParsedLink from a stored row's `raw` field.

    We don't persist the parsed pieces (anchor, label, etc.) on the row, so
    when verify/resolve runs we re-parse the `raw` to recover them. The raw
    is always exactly one link form, so `parse()` returns a single entry.
    """
    items = parse(model.raw)
    if not items:
        # Defensive fallback: synthesize a placeholder so downstream code can
        # mark the row broken instead of crashing.
        return ParsedLink(raw=model.raw, kind=LinkKind(model.kind))
    return items[0]


def _apply_resolution(
    session: Session,
    *,
    model: LinkModel,
    project_id: int,
    parsed: ParsedLink,
    mark_checked: bool,
) -> tuple[bool, bool]:
    """Compute resolution for one link and write it back.

    Returns (is_url_skipped, is_resolved_after).
    """
    kind = LinkKind(model.kind)
    if kind is LinkKind.URL:
        # No network. resolve() marks URLs as resolved=True; verify() skips.
        if not mark_checked:
            model.resolved = True
            model.broken_reason = None
        return (mark_checked, model.resolved)

    if kind is LinkKind.CANONICAL:
        ok, to_key, reason = _resolve_canonical(session, project_id, parsed.target_doc_key)
        model.to_doc_key = to_key
    elif kind is LinkKind.SECTION:
        ok, to_key, reason = _resolve_section_anchor(
            session, project_id, parsed.target_doc_key, parsed.anchor
        )
        model.to_doc_key = to_key
    elif kind is LinkKind.MARKDOWN:
        ok, to_key, reason = _resolve_canonical(session, project_id, parsed.target_doc_key)
        model.to_doc_key = to_key
    elif kind is LinkKind.WIKI:
        ok, to_key, reason = _resolve_wiki(session, project_id, parsed.target_label)
        model.to_doc_key = to_key
    elif kind is LinkKind.TASK:
        ok, to_id, reason = _resolve_task(session, project_id, parsed.target_task_id)
        model.to_task_id = to_id
    elif kind is LinkKind.STORY:
        ok, to_id, reason = _resolve_story(session, project_id, parsed.target_story_id)
        model.to_story_id = to_id
    else:
        ok, reason = False, f"unsupported kind: {kind.value}"

    model.resolved = ok
    model.broken_reason = None if ok else reason
    if mark_checked:
        model.last_checked = datetime.now(UTC)
    return (False, ok)


def sync_section(session: Session, section_id: int) -> list[Link]:
    """Replace stored `link` rows for the section from its current body parse.

    Newly inserted rows are unresolved (`to_*` left as None). Call
    `resolve_section` to populate them.
    """
    sec = _section_or_raise(session, section_id)
    project_id = _project_id_for_section(session, section_id)
    parsed = parse(sec.body)

    repo = LinkRepository(session)
    repo.delete_for_section(section_id)

    inserted: list[LinkModel] = []
    for p in parsed:
        m = LinkModel(
            project_id=project_id,
            from_section_id=section_id,
            raw=p.raw,
            kind=p.kind.value,
            to_doc_key=None,
            to_task_id=None,
            to_story_id=None,
            resolved=False,
            last_checked=None,
            broken_reason=None,
        )
        session.add(m)
        inserted.append(m)
    session.flush()

    return [repo._to_domain(m) for m in inserted]


def resolve(session: Session, link_row_id: int) -> Link:
    """Resolve a single link. Idempotent."""
    model = _link_or_raise(session, link_row_id)
    parsed = _reparse_link(model)
    _apply_resolution(
        session,
        model=model,
        project_id=model.project_id,
        parsed=parsed,
        mark_checked=False,
    )
    session.flush()
    repo = LinkRepository(session)
    return repo._to_domain(model)


def resolve_section(session: Session, section_id: int) -> list[Link]:
    """Sync (if needed) and resolve all link rows for the section."""
    repo = LinkRepository(session)
    rows = repo.list_for_section(section_id)
    if not rows:
        sync_section(session, section_id)
    project_id = _project_id_for_section(session, section_id)

    stmt = select(LinkModel).where(LinkModel.from_section_id == section_id)
    out: list[Link] = []
    for model in session.execute(stmt).scalars():
        parsed = _reparse_link(model)
        _apply_resolution(
            session,
            model=model,
            project_id=project_id,
            parsed=parsed,
            mark_checked=False,
        )
        out.append(repo._to_domain(model))
    session.flush()
    return out


def verify_section(session: Session, section_id: int) -> VerifyReport:
    """Re-check all links for a section against current DB state."""
    project_id = _project_id_for_section(session, section_id)

    stmt = select(LinkModel).where(LinkModel.from_section_id == section_id)
    ok = broken = skipped = 0
    for model in session.execute(stmt).scalars():
        if LinkKind(model.kind) is LinkKind.URL:
            skipped += 1
            continue
        parsed = _reparse_link(model)
        _, resolved = _apply_resolution(
            session,
            model=model,
            project_id=project_id,
            parsed=parsed,
            mark_checked=True,
        )
        if resolved:
            ok += 1
        else:
            broken += 1
    session.flush()
    return VerifyReport(section_id=section_id, ok=ok, broken=broken, skipped=skipped)


def list_for_section(session: Session, section_id: int) -> list[Link]:
    return LinkRepository(session).list_for_section(section_id)


def list_incoming_for_doc(
    session: Session, project_id: int, doc_key: str
) -> list[IncomingLink]:
    """COD-078: enumerate links pointing at ``doc_key`` with enough source
    context for UI rendering.

    The web layer used to reach into LinkRepository + DocumentModel + SectionModel
    directly for this; that violated the layering test. Service-side helper
    keeps the SQLAlchemy imports in infra-aware code.

    Self-links (source doc == target doc) are excluded.
    """
    rows = LinkRepository(session).list_for_doc_key(project_id, doc_key)
    out: list[IncomingLink] = []
    for link in rows:
        if link.from_section_id is None:
            continue
        section = session.get(SectionModel, link.from_section_id)
        if section is None:
            continue
        source_doc = session.get(DocumentModel, section.document_id)
        if source_doc is None or source_doc.doc_key == doc_key:
            continue
        out.append(
            IncomingLink(
                source_doc_key=source_doc.doc_key,
                source_doc_title=source_doc.title,
                section_heading=section.heading,
                section_anchor=section.anchor,
                label=_extract_label(link.raw),
            )
        )
    return out
