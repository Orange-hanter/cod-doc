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

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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

    return doc
