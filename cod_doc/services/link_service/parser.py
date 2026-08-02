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

# OBI-020: code-ref extensions. A markdown link whose href ends in one of
# these is classified as LinkKind.CODE (not LinkKind.MARKDOWN). The list
# covers the languages COD-DOC's own codebase + projects it's expected to
# document: Python, JS/TS, Go, Rust, JVM family, C/C++, Ruby, PHP, Swift,
# shell, SQL, common config formats, web frontend.
_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".pyx",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".vue",
        ".svelte",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".groovy",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".hh",
        ".cxx",
        ".rb",
        ".php",
        ".swift",
        ".m",
        ".mm",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".sql",
        ".yaml",
        ".yml",
        ".toml",
        ".json",
        ".ini",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".dockerfile",
        ".lua",
        ".pl",
        ".r",
    }
)


def _is_code_href(href: str) -> bool:
    """OBI-020: is this href a code file (vs markdown doc / URL)?"""
    if not href:
        return False
    if href.startswith(("http://", "https://", "mailto:", "tel:")):
        return False
    path = href.split("#", 1)[0]
    path = path.split("?", 1)[0]
    # Reduce to extension — handles paths like 'cod_doc/services/foo.py'.
    if "." not in path:
        return False
    ext_idx = path.rfind(".")
    ext = path[ext_idx:].lower()
    return ext in _CODE_EXTENSIONS


def _split_code_href(href: str) -> tuple[str, str | None]:
    """Return (file_path, symbol) for a code-ref href.

    Symbol comes from the ``#fragment`` after the path; ``None`` if absent.
    Path normalization mirrors ``_href_to_doc_key`` for ``./``/``../`` chains.
    """
    file_path, _, symbol = href.partition("#")
    while file_path.startswith("../") or file_path.startswith("./"):
        file_path = file_path[3:] if file_path.startswith("../") else file_path[2:]
    if file_path.startswith("/"):
        file_path = file_path[1:]
    return file_path, (symbol or None)


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
        # OBI-020: code-ref detection BEFORE doc-key parsing — files like
        # `src/auth.py` should not be treated as document keys.
        if _is_code_href(href):
            file_path, symbol = _split_code_href(href)
            out.append(
                ParsedLink(
                    raw=m.group(0),
                    kind=LinkKind.CODE,
                    target_file_path=file_path or None,
                    target_symbol=symbol,
                    start=m.start(),
                )
            )
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
