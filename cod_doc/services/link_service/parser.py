"""Pure regex-based link parser — no DB, no I/O.

Stage 1 of the link pipeline: extracts every link form from a section body
into `ParsedLink`s. Subsequent stages (resolver / verifier / cascade) use
this output to look up targets in the DB.
"""

from __future__ import annotations

import re

from cod_doc.domain.entities import LinkKind

from ._types import ParsedLink

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
                raw=raw,
                kind=LinkKind.SECTION,
                target_doc_key=doc_key or None,
                anchor=anchor or None,
                start=start,
            )
        return ParsedLink(
            raw=raw,
            kind=LinkKind.CANONICAL,
            target_doc_key=rest or None,
            start=start,
        )
    if inner.startswith("task:"):
        return ParsedLink(
            raw=raw,
            kind=LinkKind.TASK,
            target_task_id=inner[5:] or None,
            start=start,
        )
    if inner.startswith("story:"):
        return ParsedLink(
            raw=raw,
            kind=LinkKind.STORY,
            target_story_id=inner[6:] or None,
            start=start,
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
            out.append(
                ParsedLink(
                    raw=m.group(0),
                    kind=LinkKind.SECTION,
                    target_doc_key=doc_key,
                    anchor=anchor,
                    start=m.start(),
                )
            )
        elif doc_key:
            out.append(
                ParsedLink(
                    raw=m.group(0),
                    kind=LinkKind.MARKDOWN,
                    target_doc_key=doc_key,
                    start=m.start(),
                )
            )

    def _inside_md(pos: int) -> bool:
        return any(s <= pos < e for s, e in md_spans)

    for m in _BARE_URL_RE.finditer(text):
        if _inside_md(m.start()):
            continue
        url = m.group(0).rstrip(".,;:!?")  # trim trailing punctuation
        out.append(ParsedLink(raw=url, kind=LinkKind.URL, start=m.start()))

    out.sort(key=lambda p: p.start)
    return out
