"""Разбор markdown-источника — чистые функции без зависимостей от сервисов (SVC-005).

Вынесено из `import_service`: парсер нужен `doc_service` (якорь секции из
заголовка — `slugify`) и `projection_service._frontmatter` (`parse_markdown`),
а `import_service` сам пишет через `doc_service`. Пока парсер жил там, оба
потребителя тянули за собой весь импорт и замыкали кольцо
doc_service → import_service → doc_service. `import_service` реэкспортирует
`parse_markdown`, `ParsedMarkdown` и `ParsedSection`.

Что парсер умеет и чего нет — в докстринге `import_service`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

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


def slugify(text: str) -> str:
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
            anchor_base = slugify(heading)
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
