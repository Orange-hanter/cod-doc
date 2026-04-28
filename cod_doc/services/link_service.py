"""LinkService — extract / resolve / verify outgoing links between sections.

COD-013. Implements [standards/document-link.md](../../docs/system/standards/document-link.md).

Pipeline per section:
1. `parse(body)` — pure: regex-extracts every link form into `ParsedLink`s.
2. `sync_section(section_id)` — replaces stored `link` rows for the section
   with the current parse output. Newly inserted rows are unresolved.
3. `resolve_section(section_id)` — runs sync if needed, then looks up each
   target in the DB; populates `to_doc_key` / `to_task_id` / `to_story_id`
   and the `resolved` flag.
4. `verify_section(section_id)` — re-runs the lookup against the current DB
   state and stamps `last_checked` / `broken_reason`. URL links are skipped
   (no network on write-paths — see standard §7).
5. `rename_cascade(old, new)` — when a document's `doc_key` changes,
   transactionally updates link rows AND rewrites canonical-ref bodies
   `[[doc:OLD]]` / `[[doc:OLD#a]]` → `[[doc:NEW…]]`. Each rewritten section
   gets a SECTION revision via DocService.

Caller owns the transaction.

Out of scope for COD-013 (deferred):
- Cross-project refs `[[doc:project:slug/key]]`.
- Fuzzy wiki resolution (Levenshtein) — only exact title/key match.
- HTTP reachability check for URL links.
- Markdown-relative path rewrite during rename_cascade (path remap is
  brittle without document.path mapping; canonical refs are the documented
  preferred form anyway).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.domain.entities import EntityKind, Link, LinkKind
from cod_doc.infra.models import (
    DocumentModel,
    LinkModel,
    SectionModel,
    TaskModel,
    UserStoryModel,
)
from cod_doc.infra.repositories import LinkRepository
from cod_doc.services import doc_service as docs


class LinkNotFoundError(LookupError):
    pass


# --------------------------------------------------------------------------- #
# Parsed-link structure (stage 1 — pure)                                        #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Regex-based parser                                                            #
# --------------------------------------------------------------------------- #


_FENCE_RE = re.compile(r"```[A-Za-z0-9_+\-]*\n.*?```", re.DOTALL)
_WIKI_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\n]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s)\]]+")


def _strip_fenced_code(body: str) -> str:
    """Replace fenced code blocks with same-length whitespace to keep offsets."""
    return _FENCE_RE.sub(lambda m: " " * len(m.group(0)), body)


def _href_to_doc_key(href: str) -> tuple[str | None, str | None]:
    """Resolve a markdown href to (doc_key, anchor). Returns (None, None) for URLs/empties."""
    if not href:
        return None, None
    if href.startswith(("http://", "https://", "mailto:", "tel:")):
        return None, None
    anchor: str | None = None
    if "#" in href:
        href, _, anchor = href.partition("#")
    # Strip leading `../` / `./` chains.
    while href.startswith("../") or href.startswith("./"):
        href = href[3:] if href.startswith("../") else href[2:]
    if href.startswith("/"):
        href = href[1:]
    if href.endswith(".md"):
        href = href[:-3]
    return (href or None, anchor or None)


def _classify_wiki_inner(inner: str, raw: str, start: int) -> ParsedLink:
    """Classify the contents of a `[[...]]` wiki match."""
    if inner.startswith("doc:"):
        rest = inner[4:]
        if "#" in rest:
            doc_key, _, anchor = rest.partition("#")
            return ParsedLink(
                raw=raw, kind=LinkKind.SECTION,
                target_doc_key=doc_key or None, anchor=anchor or None, start=start,
            )
        return ParsedLink(
            raw=raw, kind=LinkKind.CANONICAL,
            target_doc_key=rest or None, start=start,
        )
    if inner.startswith("task:"):
        return ParsedLink(
            raw=raw, kind=LinkKind.TASK,
            target_task_id=inner[5:] or None, start=start,
        )
    if inner.startswith("story:"):
        return ParsedLink(
            raw=raw, kind=LinkKind.STORY,
            target_story_id=inner[6:] or None, start=start,
        )
    return ParsedLink(raw=raw, kind=LinkKind.WIKI, target_label=inner, start=start)


def parse(body: str) -> list[ParsedLink]:
    """Extract every link form from a section body, in order of appearance.

    Skips fenced code blocks. Handles canonical/section/task/story refs (`[[...]]`),
    plain wiki labels (`[[Title]]`), markdown links with relative paths or URLs,
    and bare URLs. Bare URLs that fall inside a markdown link are de-duped.
    """
    text = _strip_fenced_code(body)
    out: list[ParsedLink] = []
    md_spans: list[tuple[int, int]] = []

    for m in _WIKI_RE.finditer(text):
        out.append(_classify_wiki_inner(m.group(1).strip(), m.group(0), m.start()))

    for m in _MD_LINK_RE.finditer(text):
        href = m.group(2).strip()
        md_spans.append((m.start(), m.end()))
        if href.startswith(("http://", "https://")):
            out.append(ParsedLink(raw=m.group(0), kind=LinkKind.URL, start=m.start()))
            continue
        doc_key, anchor = _href_to_doc_key(href)
        if anchor:
            out.append(ParsedLink(
                raw=m.group(0), kind=LinkKind.SECTION,
                target_doc_key=doc_key, anchor=anchor, start=m.start(),
            ))
        elif doc_key:
            out.append(ParsedLink(
                raw=m.group(0), kind=LinkKind.MARKDOWN,
                target_doc_key=doc_key, start=m.start(),
            ))

    def _inside_md(pos: int) -> bool:
        return any(s <= pos < e for s, e in md_spans)

    for m in _BARE_URL_RE.finditer(text):
        if _inside_md(m.start()):
            continue
        url = m.group(0).rstrip(".,;:!?")  # trim trailing punctuation
        out.append(ParsedLink(raw=url, kind=LinkKind.URL, start=m.start()))

    out.sort(key=lambda p: p.start)
    return out


# --------------------------------------------------------------------------- #
# DB-bound helpers                                                              #
# --------------------------------------------------------------------------- #


def _section_or_raise(session: Session, section_id: int) -> SectionModel:
    model = session.get(SectionModel, section_id)
    if model is None:
        raise docs.SectionNotFoundError(f"section #{section_id}")
    return model


def _project_id_for_section(session: Session, section_id: int) -> int:
    sec = _section_or_raise(session, section_id)
    doc = session.get(DocumentModel, sec.document_id)
    assert doc is not None
    return doc.project_id


def _link_or_raise(session: Session, link_row_id: int) -> LinkModel:
    m = session.get(LinkModel, link_row_id)
    if m is None:
        raise LinkNotFoundError(f"link #{link_row_id}")
    return m


# --------------------------------------------------------------------------- #
# sync_section                                                                  #
# --------------------------------------------------------------------------- #


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

    return [repo._to_domain(m) for m in inserted]  # noqa: SLF001


# --------------------------------------------------------------------------- #
# resolve / verify — shared core                                                #
# --------------------------------------------------------------------------- #


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
        model.last_checked = datetime.now(timezone.utc)
    return (False, ok)


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
    return repo._to_domain(model)  # noqa: SLF001


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
        out.append(repo._to_domain(model))  # noqa: SLF001
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


# --------------------------------------------------------------------------- #
# rename_cascade                                                                #
# --------------------------------------------------------------------------- #


def _rewrite_canonical_refs(body: str, old_doc_key: str, new_doc_key: str) -> str:
    """Rewrite `[[doc:OLD]]` / `[[doc:OLD#anchor]]` → `[[doc:NEW…]]`.

    Markdown-relative refs are NOT rewritten here — see module docstring.
    """
    pattern = re.compile(
        r"\[\[doc:" + re.escape(old_doc_key) + r"((?:#[^\]\n]+)?)\]\]"
    )
    return pattern.sub(lambda m: f"[[doc:{new_doc_key}{m.group(1)}]]", body)


def rename_cascade(
    session: Session,
    *,
    project_id: int,
    old_doc_key: str,
    new_doc_key: str,
    author: str,
    reason: str | None = None,
) -> RenameCascadeReport:
    """Cascade-update link rows + section bodies after `doc_key` rename.

    Pre-condition: caller already updated `document.doc_key` (typically via
    `DocService.rename`). This service only fixes downstream `link` rows and
    rewrites canonical refs in section bodies, writing one SECTION revision
    per modified section.
    """
    if old_doc_key == new_doc_key:
        return RenameCascadeReport(
            old_doc_key=old_doc_key, new_doc_key=new_doc_key,
            updated_links=0, rewritten_sections=0,
        )

    # 1. Update all link rows whose target was the old doc_key.
    link_stmt = select(LinkModel).where(
        LinkModel.project_id == project_id,
        LinkModel.to_doc_key == old_doc_key,
    )
    affected_links = list(session.execute(link_stmt).scalars())
    affected_section_ids: set[int] = set()
    for link in affected_links:
        link.to_doc_key = new_doc_key
        link.raw = _rewrite_canonical_refs(link.raw, old_doc_key, new_doc_key)
        affected_section_ids.add(link.from_section_id)
    if affected_links:
        session.flush()

    # 2. Rewrite section bodies for sections that hosted any old-key reference,
    #    and write SECTION revisions.
    rewritten = 0
    for section_id in sorted(affected_section_ids):
        sec = session.get(SectionModel, section_id)
        if sec is None:  # section deleted concurrently
            continue
        new_body = _rewrite_canonical_refs(sec.body, old_doc_key, new_doc_key)
        if new_body == sec.body:
            continue
        docs.patch_section(
            session,
            document_id=sec.document_id,
            anchor=sec.anchor,
            new_body=new_body,
            author=author,
            reason=reason or f"rename_cascade {old_doc_key} → {new_doc_key}",
        )
        rewritten += 1

    return RenameCascadeReport(
        old_doc_key=old_doc_key,
        new_doc_key=new_doc_key,
        updated_links=len(affected_links),
        rewritten_sections=rewritten,
    )


# Re-export EntityKind for convenience callers. (Not strictly needed.)
__all__ = [
    "EntityKind",
    "LinkNotFoundError",
    "ParsedLink",
    "RenameCascadeReport",
    "VerifyReport",
    "list_for_section",
    "parse",
    "rename_cascade",
    "resolve",
    "resolve_section",
    "sync_section",
    "verify_section",
]
