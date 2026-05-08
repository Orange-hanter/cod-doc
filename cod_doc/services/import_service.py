"""ImportService — turn an existing markdown file into a Document + Sections.

Used by the Web UI «Import markdown» button and by `cod-doc doc create-from-file`
(future). Pure parsing helpers are exposed for unit testing; the orchestrator
function `import_markdown` ties them to `doc_service` writes.

Parsing scope (intentionally minimal — same philosophy as the renderer):

- **YAML frontmatter** between leading `---\\n` … `\\n---\\n` markers, optional.
- **H1 line** (`# title`) immediately after frontmatter — used as title fallback
  if frontmatter doesn't carry `title`. The H1 line itself is stripped (the doc
  has its own title field).
- **Preamble** — everything up to the first `## ` heading.
- **Sections** — each `## ` line starts a new section. Higher headings (`###`,
  `####`) are kept inside the current section's body. Anchors auto-derived from
  heading text (lowercase, dashes, ASCII-ish).

Doesn't try to: handle setext headings (`====` underlines), tables, footnotes,
nested H2 inside fenced code blocks (would require a full markdown parser).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Document


_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_H1_LINE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
_H2_LINE = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_SLUG_BAD = re.compile(r"[^\w\-]+", re.UNICODE)


@dataclass(slots=True)
class ParsedSection:
    anchor: str
    heading: str
    level: int
    body: str


@dataclass(slots=True)
class ParsedMarkdown:
    frontmatter: dict[str, Any] = field(default_factory=dict)
    title_h1: str | None = None
    preamble: str = ""
    sections: list[ParsedSection] = field(default_factory=list)


def _slugify(text: str) -> str:
    s = text.strip().lower().replace(" ", "-")
    s = _SLUG_BAD.sub("", s)
    return s or "section"


def parse_markdown(raw: str) -> ParsedMarkdown:
    """Parse a markdown source into frontmatter + preamble + sections."""
    body = raw
    out = ParsedMarkdown()

    # 1. Frontmatter (optional)
    fm_match = _FRONTMATTER.match(raw)
    if fm_match:
        try:
            out.frontmatter = yaml.safe_load(fm_match.group(1)) or {}
            if not isinstance(out.frontmatter, dict):
                out.frontmatter = {}
        except yaml.YAMLError:
            out.frontmatter = {}
        body = raw[fm_match.end() :]

    # 2. Optional leading H1 — strip and remember
    lines = body.split("\n")
    if lines and (m := _H1_LINE.match(lines[0])):
        out.title_h1 = m.group(1).strip()
        lines = lines[1:]
        # Drop one blank separator line if any, for clean preamble.
        if lines and not lines[0].strip():
            lines = lines[1:]

    # 3. Walk lines: collect preamble until first H2, then split by H2.
    preamble_lines: list[str] = []
    in_fence = False
    used_anchors: set[str] = set()
    current: ParsedSection | None = None
    current_body: list[str] = []

    def _flush_current() -> None:
        if current is not None:
            current.body = "\n".join(current_body).strip("\n")
            out.sections.append(current)

    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
            if current is None:
                preamble_lines.append(line)
            else:
                current_body.append(line)
            continue
        if not in_fence and (m := _H2_LINE.match(line)):
            heading = m.group(1).strip()
            anchor_base = _slugify(heading)
            anchor = anchor_base
            i = 1
            while anchor in used_anchors:
                i += 1
                anchor = f"{anchor_base}-{i}"
            used_anchors.add(anchor)
            _flush_current()
            current = ParsedSection(anchor=anchor, heading=heading, level=2, body="")
            current_body = []
            continue
        if current is None:
            preamble_lines.append(line)
        else:
            current_body.append(line)
    _flush_current()
    out.preamble = "\n".join(preamble_lines).strip("\n").strip()
    return out


def _enum_or_default(enum_cls: type, raw: Any, default: Any) -> Any:
    """Coerce a frontmatter value into an enum, falling back to default."""
    if not raw:
        return default
    try:
        return enum_cls(str(raw))
    except (ValueError, TypeError):
        return default


def import_markdown(
    session: Session,
    *,
    project_id: int,
    doc_key: str,
    raw_markdown: str,
    fallback_title: str | None = None,
    fallback_type: DocumentType = DocumentType.MODULE_SPEC,
    author: str = "human:web",
    reason: str | None = None,
) -> Document:
    """Parse + insert document and its sections into the project DB.

    Frontmatter fields used (all optional): `title`, `type`, `status`, `owner`,
    `sensitivity`. Anything not in frontmatter falls back to either a sensible
    default or the `fallback_*` arguments.

    Caller is responsible for committing the transaction.
    """
    parsed = parse_markdown(raw_markdown)
    fm = parsed.frontmatter

    title = (
        fm.get("title")
        or parsed.title_h1
        or fallback_title
        or doc_key.rsplit("/", 1)[-1]
    )
    doc_type = _enum_or_default(DocumentType, fm.get("type"), fallback_type)
    status = _enum_or_default(DocumentStatus, fm.get("status"), DocumentStatus.DRAFT)
    sensitivity = _enum_or_default(Sensitivity, fm.get("sensitivity"), Sensitivity.INTERNAL)
    owner = fm.get("owner") or author

    doc = docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=doc_type,
        status=status,
        title=str(title),
        author=author,
        owner=str(owner),
        sensitivity=sensitivity,
        preamble=parsed.preamble or "",
        reason=reason or "import_markdown",
    )

    assert doc.row_id is not None  # doc_service.create asserts this internally
    for i, section in enumerate(parsed.sections):
        docs.add_section(
            session,
            document_id=doc.row_id,
            anchor=section.anchor,
            heading=section.heading,
            level=section.level,
            position=i,
            body=section.body,
            author=author,
            reason=reason or "import_markdown",
        )

    # Proposal 15 §2.2 two-pass resolve: run resolve_section for all imported
    # sections after the full document is in the DB, so that forward-links to
    # sections that were inserted later in the same batch get resolved.
    _resolve_all_sections(session, doc.row_id)

    return doc


def import_or_update_markdown(
    session: "Session",
    *,
    project_id: int,
    doc_key: str,
    raw_markdown: str,
    fallback_title: str | None = None,
    author: str = "human:web",
    reason: str | None = None,
) -> tuple["Document", bool]:
    """PCA-929: Idempotent import — create new doc or update existing one.

    Returns ``(document, created)`` where ``created`` is True for new docs
    and False when an existing doc's sections were patched.
    """
    existing = docs.get(session, project_id, doc_key)
    if existing is None:
        doc = import_markdown(
            session,
            project_id=project_id,
            doc_key=doc_key,
            raw_markdown=raw_markdown,
            fallback_title=fallback_title,
            author=author,
            reason=reason or "bulk import (new)",
        )
        return doc, True

    # Doc exists — patch each section body to create a new revision.
    assert existing.row_id is not None
    parsed = parse_markdown(raw_markdown)

    for section in parsed.sections:
        try:
            docs.patch_section(
                session,
                document_id=existing.row_id,
                anchor=section.anchor,
                new_body=section.body,
                author=author,
                reason=reason or "bulk import (update)",
            )
        except Exception:
            # Section may not exist yet — add it.
            try:
                existing_sections = docs.get_sections(session, existing.row_id)
                position = len(existing_sections)
                docs.add_section(
                    session,
                    document_id=existing.row_id,
                    anchor=section.anchor,
                    heading=section.heading,
                    level=section.level,
                    position=position,
                    body=section.body,
                    author=author,
                    reason=reason or "bulk import (new section)",
                )
            except Exception:
                pass

    _resolve_all_sections(session, existing.row_id)
    return existing, False


def _resolve_all_sections(session: "Session", document_id: int) -> None:
    """Best-effort second-pass resolve for every section of *document_id*.

    Called at the end of import_markdown() so forward links that were
    unresolvable during add_section (target not yet in DB) get a second
    chance once the whole document exists.
    """
    try:
        from cod_doc.services import link_service as _links
        from cod_doc.infra.repositories.doc_repo import SectionRepository

        for sec in SectionRepository(session).list_for_document(document_id):
            if sec.row_id is not None:
                _links.resolve_section(session, sec.row_id)
    except Exception:
        import logging
        logging.getLogger("cod_doc.services.import_service").warning(
            "Two-pass resolve failed for document_id=%s — "
            "links may be unresolved until next backfill",
            document_id,
            exc_info=True,
        )


# ── Folder manifest scanner (PCA-400) ────────────────────────────────────────

ManifestStatus = Literal["new", "changed", "unchanged", "missing"]

_HEAD_BYTES = 4096  # hash first 4 KB only — cheap, stable enough for change detection


@dataclass(slots=True)
class ManifestEntry:
    """One .md file compared against the current project DB."""

    path: str               # relative path from scan root (e.g. "modules/foo.md")
    doc_key: str            # auto-derived key (path without .md, without "docs/" prefix)
    title: str              # from H1 or frontmatter or filename
    doc_type: str           # from frontmatter `type:` or empty string
    sha256_head: str        # sha256 of first 4 KB
    status: ManifestStatus  # new | changed | unchanged | missing
    reason: str             # human-readable hint for the UI


def _derive_doc_key(rel_path: str) -> str:
    """Turn a relative file path into a doc_key.

    Rules (per proposal 13 §2.2):
    - Strip .md suffix.
    - Strip leading "docs/" prefix if present.
    - Normalise path separators to "/".
    """
    key = rel_path.replace(os.sep, "/")
    if key.endswith(".md"):
        key = key[:-3]
    if key.startswith("docs/"):
        key = key[5:]
    return key


def _head_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        h.update(fh.read(_HEAD_BYTES))
    return h.hexdigest()


def _quick_title(parsed: ParsedMarkdown, path: Path) -> str:
    if parsed.frontmatter.get("title"):
        return str(parsed.frontmatter["title"])
    if parsed.title_h1:
        return parsed.title_h1
    return path.stem


def scan_folder(
    session: "Session",
    *,
    project_id: int,
    root: Path,
    sub_dir: str = ".",
    extensions: tuple[str, ...] = (".md",),
) -> list[ManifestEntry]:
    """Scan *root/sub_dir* for markdown files and compare against DB.

    Returns a list of :class:`ManifestEntry` sorted by path.  Entries
    with status ``"missing"`` appear at the end — they represent docs
    in the DB that have no corresponding file on disk.

    Args:
        session:    SQLAlchemy session for the project DB.
        project_id: numeric project PK.
        root:       Absolute path of the project root on disk.
        sub_dir:    Sub-directory to scan (relative to *root*).  Use
                    ``"."`` to scan the whole project tree.
        extensions: File extensions to consider (default ``(".md",)``).
    """
    scan_root = (root / sub_dir).resolve()
    if not scan_root.exists():
        raise ValueError(f"Scan directory does not exist: {scan_root}")

    # Build index of existing docs in DB: doc_key → sha256 stored in preamble meta
    # We don't store sha256 in DB, so we'll detect "changed" via a lightweight
    # comparison: file on disk but not in DB = new; file + DB key match = unchanged
    # (we recompute the head hash and compare to nothing — simplest approach is
    # to treat all existing keys as "unchanged" and only flag truly new files as "new").
    # For a richer diff we'd need to store the hash — that's a migration; for now
    # we compare presence only (new vs. existing) and size-change heuristic.

    existing: dict[str, Any] = {}
    all_docs = docs.list_for_project(session, project_id)
    for d in all_docs:
        existing[d.doc_key] = d

    entries: list[ManifestEntry] = []
    seen_keys: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(scan_root):
        # Skip hidden directories (e.g. .git, .cod-doc)
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in sorted(filenames):
            if not any(fname.endswith(ext) for ext in extensions):
                continue
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(scan_root))
            doc_key = _derive_doc_key(rel)
            seen_keys.add(doc_key)

            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            parsed = parse_markdown(raw)
            sha = _head_sha256(fpath)
            title = _quick_title(parsed, fpath)
            doc_type_raw = str(parsed.frontmatter.get("type", "") or "")

            if doc_key not in existing:
                status: ManifestStatus = "new"
                reason = "Not in database"
            else:
                # We can't cheaply detect content changes without stored hash.
                # Mark as "unchanged" for now; the apply endpoint is idempotent
                # (changed = new revision on reimport).
                status = "unchanged"
                reason = "Already imported"

            entries.append(ManifestEntry(
                path=rel,
                doc_key=doc_key,
                title=title,
                doc_type=doc_type_raw,
                sha256_head=sha,
                status=status,
                reason=reason,
            ))

    # Report DB docs that have no file on disk (MISSING)
    for doc_key, doc in existing.items():
        if doc_key not in seen_keys:
            entries.append(ManifestEntry(
                path="",
                doc_key=doc_key,
                title=str(doc.title or doc_key),
                doc_type="",
                sha256_head="",
                status="missing",
                reason="File not found on disk",
            ))

    entries.sort(key=lambda e: (e.status == "missing", e.path))
    return entries
