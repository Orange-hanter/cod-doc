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
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import yaml
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.models import DocumentModel
from cod_doc.services import doc_service as docs
from cod_doc.services import search_service

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Document

logger = logging.getLogger("cod_doc.import_service")


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
    # ADO-010: the YAML block exactly as written, without the `---` fences and
    # without a trailing newline. `frontmatter` is the parsed form; this is what
    # export re-emits so key order / flow style / unquoted dates survive.
    frontmatter_raw: str | None = None
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
        out.frontmatter_raw = fm_match.group(1)
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


# ── ADO-015: coercion that reports itself ────────────────────────────────────

CoercionReason = Literal["unknown", "alias"]

_ENUM = TypeVar("_ENUM", bound=StrEnum)


@dataclass(slots=True, frozen=True)
class CoercedField:
    """One frontmatter value the import could not store as written.

    Named `CoercedField`, not `ImportWarning` — the latter shadows a Python
    builtin exception.
    """

    field: str  # frontmatter key: "type" | "status" | "sensitivity"
    raw: str  # value exactly as the file wrote it
    applied: str  # value actually written to the DB
    reason: CoercionReason  # "alias" — known foreign spelling; "unknown" — fallback

    def to_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "raw": self.raw,
            "applied": self.applied,
            "reason": self.reason,
        }

    def describe(self) -> str:
        """One line fit for a CLI / log — `type: 'kickoff-brief' → 'module-spec'`."""
        note = "unknown value" if self.reason == "unknown" else "foreign spelling"
        return f"{self.field}: {self.raw!r} → {self.applied!r} ({note})"


@dataclass(slots=True)
class ImportReport:
    """What an import did — including every value it had to bend.

    Before ADO-015 both import entry points returned only the document, so a
    frontmatter `type: capability` became `module-spec` in the DB with nothing
    anywhere saying so. Callers now get `warnings` whether they ask or not.
    """

    document: Document
    created: bool
    warnings: list[CoercedField] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_key": self.document.doc_key,
            "document_id": self.document.row_id,
            "created": self.created,
            "warnings": [w.to_dict() for w in self.warnings],
        }


# Foreign status vocabularies met in imported corpora, mapped onto the four
# cod-doc statuses. A table, not a chain of `if`s, so the mapping is greppable
# and testable; every substitution still raises a warning (reason="alias") —
# a documented rename is not a licence to stay silent.
_ALIEN_STATUS_ALIASES: dict[str, DocumentStatus] = {
    # «живой» / завершённый документ чужого стандарта — работающий, не черновик
    "living": DocumentStatus.ACTIVE,
    "final": DocumentStatus.ACTIVE,
    "done": DocumentStatus.ACTIVE,
    "complete": DocumentStatus.ACTIVE,
    "completed": DocumentStatus.ACTIVE,
    # закрытый аудит: все задачи разобраны, но отчёт остаётся действительным
    # документом. Не `deprecated` — иначе 9 наших собственных audit-report'ов
    # уезжают в «снят с эксплуатации» на первом же импорте.
    "resolved": DocumentStatus.ACTIVE,
    "accepted": DocumentStatus.ACTIVE,
    "delivered": DocumentStatus.ACTIVE,
    "published": DocumentStatus.ACTIVE,
    "current": DocumentStatus.ACTIVE,
    "stable": DocumentStatus.ACTIVE,
    "in-progress": DocumentStatus.ACTIVE,
    "in_progress": DocumentStatus.ACTIVE,
    # ещё не принят
    "proposed": DocumentStatus.DRAFT,
    "pending": DocumentStatus.DRAFT,
    "wip": DocumentStatus.DRAFT,
    "todo": DocumentStatus.DRAFT,
    # на рассмотрении
    "in-review": DocumentStatus.REVIEW,
    "in_review": DocumentStatus.REVIEW,
    "reviewing": DocumentStatus.REVIEW,
    # снят с эксплуатации
    "archived": DocumentStatus.DEPRECATED,
    "superseded": DocumentStatus.DEPRECATED,
    "obsolete": DocumentStatus.DEPRECATED,
    "rejected": DocumentStatus.DEPRECATED,
    "cancelled": DocumentStatus.DEPRECATED,
}


def _coerce_enum(
    enum_cls: type[_ENUM],
    raw: object,
    default: _ENUM,
    *,
    field_name: str,
    sink: list[CoercedField],
    aliases: Mapping[str, _ENUM] | None = None,
) -> _ENUM:
    """Coerce a frontmatter value into *enum_cls*, recording what was bent.

    An absent or empty value falls back to *default* in silence — that is a
    default, not a substitution. A value that was written and could not be
    stored as written always lands in *sink*.
    """
    if not raw:
        return default
    text = str(raw).strip()
    if not text:
        return default
    try:
        return enum_cls(text)
    except (ValueError, TypeError):
        pass
    alias = (aliases or {}).get(text.lower())
    if alias is not None:
        sink.append(CoercedField(field=field_name, raw=text, applied=alias.value, reason="alias"))
        return alias
    sink.append(CoercedField(field=field_name, raw=text, applied=default.value, reason="unknown"))
    return default


def _jsonable_frontmatter(value: Any) -> Any:
    """Convert YAML-loaded frontmatter values into JSON-storable values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable_frontmatter(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable_frontmatter(v) for v in value]
    return value


def _set_projection_shape(session: Session, document_id: int, parsed: ParsedMarkdown) -> None:
    """ADO-010: record how the source file was shaped, so export can rebuild it.

    Kept off `doc_service.create` on purpose: these are projection-fidelity
    artifacts of the *file*, not part of the document's domain identity.

    An imported file with no frontmatter block stores `""`, which is distinct
    from `NULL` (never imported — a DB-authored document). The renderer uses
    that difference to decide whether emitting frontmatter would be restoring
    metadata or inventing it; `title_in_body` plays the same role for the H1.
    """
    model = session.get(DocumentModel, document_id)
    if model is not None:
        model.frontmatter_raw = parsed.frontmatter_raw or ""
        model.title_in_body = parsed.title_h1 is not None
        session.flush()


def _frontmatter_with_effective_defaults(
    frontmatter: dict[str, Any], *, sensitivity: Sensitivity
) -> dict[str, Any]:
    """Store DB-effective defaults required by metadata audit."""
    stored = dict(frontmatter)
    stored.setdefault("sensitivity", sensitivity.value)
    return stored


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
    path: str | None = None,
) -> ImportReport:
    """Parse + insert document and its sections into the project DB.

    Frontmatter fields used (all optional): `title`, `type`, `status`, `owner`,
    `sensitivity`. Anything not in frontmatter falls back to either a sensible
    default or the `fallback_*` arguments.

    ADO-015: a frontmatter value that had to be bent to fit an enum is
    reported in `ImportReport.warnings` instead of vanishing.

    Caller is responsible for committing the transaction.
    """
    parsed = parse_markdown(raw_markdown)
    fm = _jsonable_frontmatter(parsed.frontmatter)
    warnings: list[CoercedField] = []

    title = fm.get("title") or parsed.title_h1 or fallback_title or doc_key.rsplit("/", 1)[-1]
    doc_type = _coerce_enum(
        DocumentType, fm.get("type"), fallback_type, field_name="type", sink=warnings
    )
    status = _coerce_enum(
        DocumentStatus,
        fm.get("status"),
        DocumentStatus.DRAFT,
        field_name="status",
        sink=warnings,
        aliases=_ALIEN_STATUS_ALIASES,
    )
    sensitivity = _coerce_enum(
        Sensitivity,
        fm.get("sensitivity"),
        Sensitivity.INTERNAL,
        field_name="sensitivity",
        sink=warnings,
    )
    owner = fm.get("owner") or author
    stored_frontmatter = _frontmatter_with_effective_defaults(fm, sensitivity=sensitivity)

    doc = docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=doc_type,
        status=status,
        title=str(title),
        author=author,
        owner=str(owner),
        path=path,
        source_of_truth=(
            fm.get("source_of_truth") if isinstance(fm.get("source_of_truth"), bool) else None
        ),
        sensitivity=sensitivity,
        preamble=parsed.preamble or "",
        frontmatter=stored_frontmatter,
        reason=reason or "import_markdown",
    )

    assert doc.row_id is not None  # doc_service.create asserts this internally
    _set_projection_shape(session, doc.row_id, parsed)
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

    return ImportReport(document=doc, created=True, warnings=warnings)


def import_or_update_markdown(
    session: Session,
    *,
    project_id: int,
    doc_key: str,
    raw_markdown: str,
    fallback_title: str | None = None,
    author: str = "human:web",
    reason: str | None = None,
    source_sha256: str | None = None,
    path: str | None = None,
) -> ImportReport:
    """PCA-929: Idempotent import — create new doc or update existing one.

    Returns an :class:`ImportReport`; ``report.created`` is True for new docs
    and False when an existing doc's sections were patched.

    ADO-015: the update path reports coerced frontmatter too — it used to be
    the worse of the two, re-stamping the same silent fallback on every
    re-import until it looked like the author's own choice.

    PCA-928: when ``source_sha256`` is provided, it is stored on the
    DocumentModel.content_sha256_head column for change detection later.
    """
    existing = docs.get(session, project_id, doc_key)
    if existing is None:
        report = import_markdown(
            session,
            project_id=project_id,
            doc_key=doc_key,
            raw_markdown=raw_markdown,
            fallback_title=fallback_title,
            author=author,
            reason=reason or "bulk import (new)",
            path=path,
        )
        doc_row_id = report.document.row_id
        if source_sha256 is not None and doc_row_id is not None:
            _set_content_sha(session, doc_row_id, source_sha256)
            _set_projection_hash_to_rendered(session, doc_row_id)
        # ADO-030: keep FTS fresh in the same transaction — a doc you just
        # imported must be searchable without a manual --reindex.
        search_service.upsert_doc(session, project_id=project_id, doc_key=doc_key)
        return report

    # Doc exists — first sync its document-level metadata/frontmatter, then
    # patch each section body to create section revisions when needed.
    assert existing.row_id is not None
    parsed = parse_markdown(raw_markdown)
    warnings: list[CoercedField] = []
    _update_existing_document_metadata(
        session,
        document_id=existing.row_id,
        parsed=parsed,
        fallback_title=fallback_title,
        author=author,
        sink=warnings,
    )

    for section in parsed.sections:
        # ADO-055: «секции нет» — единственный легальный повод для fallback на
        # add_section. Любой другой провал patch/add логируется и попадает в
        # ImportReport.warnings — импорт не падает на одной секции, но и не
        # теряет её молча.
        try:
            docs.patch_section(
                session,
                document_id=existing.row_id,
                anchor=section.anchor,
                new_body=section.body,
                author=author,
                reason=reason or "bulk import (update)",
            )
            continue
        except docs.SectionNotFoundError:
            pass  # секции ещё нет — добавим ниже
        except Exception as exc:
            logger.warning("import %s#%s: patch_section failed: %s", doc_key, section.anchor, exc)
            warnings.append(
                CoercedField(
                    field=f"section:{section.anchor}",
                    raw=type(exc).__name__,
                    applied="skipped",
                    reason="unknown",
                )
            )
            continue

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
        except Exception as exc:
            logger.warning("import %s#%s: add_section failed: %s", doc_key, section.anchor, exc)
            warnings.append(
                CoercedField(
                    field=f"section:{section.anchor}",
                    raw=type(exc).__name__,
                    applied="skipped",
                    reason="unknown",
                )
            )

    _resolve_all_sections(session, existing.row_id)
    if source_sha256 is not None:
        _set_content_sha(session, existing.row_id, source_sha256)
        _set_projection_hash_to_rendered(session, existing.row_id)
    # ADO-030: same-transaction FTS refresh on the update path too.
    search_service.upsert_doc(session, project_id=project_id, doc_key=doc_key)
    refreshed = docs.get(session, project_id, doc_key) or existing
    return ImportReport(document=refreshed, created=False, warnings=warnings)


def _update_existing_document_metadata(
    session: Session,
    *,
    document_id: int,
    parsed: ParsedMarkdown,
    fallback_title: str | None,
    author: str,
    sink: list[CoercedField],
) -> None:
    """Apply parsed frontmatter/preamble to an existing Document row.

    Earlier imports updated only section bodies, which meant DB records could
    drift from markdown frontmatter (`status`, `source_of_truth`,
    `canonical_source`, `sensitivity`, `owner`, etc.). The markdown file is
    the import source for this operation; after it runs, the DB row is the
    canonical structured representation.

    ADO-015: *sink* collects every coercion, so a re-import cannot quietly
    re-apply yesterday's fallback as if the author had asked for it.
    """
    model = session.execute(
        select(DocumentModel).where(DocumentModel.row_id == document_id)
    ).scalar_one()
    fm = _jsonable_frontmatter(parsed.frontmatter)

    title = fm.get("title") or parsed.title_h1 or fallback_title or model.title
    model.title = str(title)
    model.type = _coerce_enum(
        DocumentType,
        fm.get("type"),
        DocumentType(model.type),
        field_name="type",
        sink=sink,
    ).value
    model.status = _coerce_enum(
        DocumentStatus,
        fm.get("status"),
        DocumentStatus(model.status),
        field_name="status",
        sink=sink,
        aliases=_ALIEN_STATUS_ALIASES,
    ).value
    model.sensitivity = _coerce_enum(
        Sensitivity,
        fm.get("sensitivity"),
        Sensitivity(model.sensitivity),
        field_name="sensitivity",
        sink=sink,
    ).value
    if "owner" in fm:
        model.owner = str(fm["owner"]) if fm["owner"] else None
    elif not model.owner:
        model.owner = author
    if isinstance(fm.get("source_of_truth"), bool):
        model.source_of_truth = bool(fm["source_of_truth"])
    model.preamble = parsed.preamble or ""
    model.frontmatter_raw = parsed.frontmatter_raw or ""
    model.title_in_body = parsed.title_h1 is not None
    model.frontmatter_json = _frontmatter_with_effective_defaults(
        fm, sensitivity=Sensitivity(model.sensitivity)
    )
    model.last_updated = datetime.now(UTC)
    session.flush()


def _set_content_sha(session: Session, document_id: int, sha: str) -> None:
    """PCA-928: store sha256 of the accepted imported file on DocumentModel."""
    from sqlalchemy import update as _update

    from cod_doc.infra.models.documents import DocumentModel

    session.execute(
        _update(DocumentModel)
        .where(DocumentModel.row_id == document_id)
        .values(content_sha256_head=sha)
    )


def _set_projection_hash_to_rendered(session: Session, document_id: int) -> None:
    """Record the current DB-render hash as the accepted DB baseline."""
    from sqlalchemy import update as _update

    from cod_doc.infra.models.documents import DocumentModel
    from cod_doc.services.projection_service._safety import _sha256
    from cod_doc.services.projection_service.render import render_markdown

    rendered_hash = _sha256(render_markdown(session, document_id))
    session.execute(
        _update(DocumentModel)
        .where(DocumentModel.row_id == document_id)
        .values(projection_hash=rendered_hash)
    )


def _resolve_all_sections(session: Session, document_id: int) -> None:
    """Best-effort second-pass resolve for every section of *document_id*.

    Called at the end of import_markdown() so forward links that were
    unresolvable during add_section (target not yet in DB) get a second
    chance once the whole document exists.
    """
    try:
        from cod_doc.infra.repositories import SectionRepository
        from cod_doc.services import link_service as _links

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


@dataclass(slots=True)
class ManifestEntry:
    """One .md file compared against the current project DB."""

    path: str  # relative path from scan root (e.g. "modules/foo.md")
    doc_key: str  # auto-derived key (path without .md, without "docs/" prefix)
    title: str  # from H1 or frontmatter or filename
    doc_type: str  # from frontmatter `type:` or empty string
    sha256_head: str  # full-file sha256 (field name kept for API compat)
    status: ManifestStatus  # new | changed | unchanged | missing
    reason: str  # human-readable hint for the UI


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


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _quick_title(parsed: ParsedMarkdown, path: Path) -> str:
    if parsed.frontmatter.get("title"):
        return str(parsed.frontmatter["title"])
    if parsed.title_h1:
        return parsed.title_h1
    return path.stem


def scan_folder(
    session: Session,
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

    # PCA-928: load DocumentModel directly so we can read content_sha256_head
    # for content-change detection.
    from sqlalchemy import select as _select

    from cod_doc.infra.models.documents import DocumentModel

    existing: dict[str, Any] = {}
    rows = session.execute(
        _select(DocumentModel).where(DocumentModel.project_id == project_id)
    ).scalars()
    for d in rows:
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
            sha = _file_sha256(fpath)
            title = _quick_title(parsed, fpath)
            doc_type_raw = str(parsed.frontmatter.get("type", "") or "")

            if doc_key not in existing:
                status: ManifestStatus = "new"
                reason = "Not in database"
            else:
                # PCA-928/ADO-026: compare stored full-file sha256 with disk.
                stored_sha = getattr(existing[doc_key], "content_sha256_head", None)
                if stored_sha is None:
                    status = "unchanged"
                    reason = "Imported (no sha recorded — pre-PCA-928)"
                elif stored_sha == sha:
                    status = "unchanged"
                    reason = "File unchanged since last import"
                else:
                    status = "changed"
                    reason = "File modified since last import"

            entries.append(
                ManifestEntry(
                    path=rel,
                    doc_key=doc_key,
                    title=title,
                    doc_type=doc_type_raw,
                    sha256_head=sha,
                    status=status,
                    reason=reason,
                )
            )

    # Report DB docs that have no file on disk (MISSING)
    for doc_key, doc in existing.items():
        if doc_key not in seen_keys:
            entries.append(
                ManifestEntry(
                    path="",
                    doc_key=doc_key,
                    title=str(doc.title or doc_key),
                    doc_type="",
                    sha256_head="",
                    status="missing",
                    reason="File not found on disk",
                )
            )

    entries.sort(key=lambda e: (e.status == "missing", e.path))
    return entries
