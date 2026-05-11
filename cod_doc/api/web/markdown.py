r"""Tiny safe-by-default markdown renderer (WEB-006 + heading/blockquote).

Scope grew to cover MASTER.md preview on the project overview, in addition
to section bodies. Block-level features:
- ATX headings `# … ######`
- Bullet lists (`- ` / `* `)
- Ordered lists (`1. ` / `1) `, respects start number for resumed lists)
- Code fences ``` … ``` (language tag discarded; preserves whitespace)
- Blockquotes `> …` (collapsible consecutive lines)
- GFM tables: header `|`-row + delimiter `|---|:--:|` + body rows (COD-079)
- Paragraphs separated by blank lines

What we still DO NOT render: footnotes, images, nested lists, GFM task-lists.

Inline features (escaped first, then matched):
- `code`, **bold**, *italic*, [text](url)
- Markdown-active chars inside backticks are entity-shielded so `*foo*`
  inside `\`code\`` doesn't become italics inside <code>.

Adopted instead of pulling in `markdown-it-py` (~50 KB + transitive deps) to
honor capability §2 («no new deps without justification»).

Safety: every line is HTML-escaped before any markdown pattern runs — raw
HTML in user input cannot smuggle through.
"""

from __future__ import annotations

import re
from html import escape as html_escape

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_OL_ITEM = re.compile(r"^(\d+)[.)]\s+(.+)$")
# GFM table delimiter cell: optional leading/trailing colon (alignment), 1+ dashes.
_TABLE_DELIM_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")


def _shield_inline_code(match: re.Match[str]) -> str:
    """Replace markdown-active chars inside `code` with HTML entities so
    later regex passes don't peek inside. Underscore is NOT shielded —
    we don't support `_italic_` syntax, and shielding it leaks ugly
    `&#95;` into source HTML for code snippets like `idempotency_key`.
    """
    content = match.group(1)
    for ch, entity in (("*", "&#42;"), ("[", "&#91;"), ("`", "&#96;")):
        content = content.replace(ch, entity)
    return f"<code>{content}</code>"


def _render_inline(text: str) -> str:
    """Escape and apply inline markdown to a single line / paragraph."""
    text = html_escape(text)
    # Code first, with content shielded — so * inside backticks isn't bolded.
    text = _INLINE_CODE.sub(_shield_inline_code, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    return text


def _split_pipe_row(line: str) -> list[str]:
    """Split a GFM table row on `|`, ignoring leading/trailing pipe."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _parse_table_alignments(delim_line: str) -> list[str] | None:
    """Return per-column alignment ('left'|'right'|'center'|None) or None
    when ``delim_line`` is not a valid GFM delimiter row."""
    cells = _split_pipe_row(delim_line)
    if not cells or any(not _TABLE_DELIM_CELL.match(c) for c in cells):
        return None
    aligns: list[str] = []
    for raw in cells:
        c = raw.strip()
        left = c.startswith(":")
        right = c.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        elif left:
            aligns.append("left")
        else:
            aligns.append("")
    return aligns


def _try_render_table(lines: list[str], i: int) -> tuple[str, int] | None:
    """If ``lines[i:]`` starts with a GFM table, render it; return
    ``(html, end_index_exclusive)``. Otherwise None.

    The caller should fall back to its normal line-by-line dispatch.
    """
    if i + 1 >= len(lines):
        return None
    head = lines[i]
    if "|" not in head or not head.strip():
        return None
    aligns = _parse_table_alignments(lines[i + 1])
    if aligns is None:
        return None
    headers = _split_pipe_row(head)
    if len(headers) != len(aligns):
        return None

    rows: list[list[str]] = []
    j = i + 2
    while j < len(lines):
        line = lines[j]
        if not line.strip() or "|" not in line:
            break
        cells = _split_pipe_row(line)
        # Pad / trim to the header width so jagged rows still render.
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        elif len(cells) > len(headers):
            cells = cells[: len(headers)]
        rows.append(cells)
        j += 1

    def _cell_attr(idx: int) -> str:
        align = aligns[idx] if idx < len(aligns) else ""
        return f' style="text-align:{align}"' if align else ""

    out: list[str] = ['<table class="md-table">']
    out.append("<thead><tr>")
    for idx, h in enumerate(headers):
        out.append(f"<th{_cell_attr(idx)}>{_render_inline(h)}</th>")
    out.append("</tr></thead>")
    if rows:
        out.append("<tbody>")
        for row in rows:
            out.append("<tr>")
            for idx, cell in enumerate(row):
                out.append(f"<td{_cell_attr(idx)}>{_render_inline(cell)}</td>")
            out.append("</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out), j


def render_markdown(text: str) -> str:
    """Render markdown text into HTML. Returns "" for falsy inputs."""
    if not text:
        return ""

    blocks: list[str] = []
    in_fence = False
    fence_lang: str = ""
    fence_lines: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []
    ol_items: list[str] = []
    ol_start: list[int] = [1]  # mutable so inner closures can write without nonlocal
    blockquote_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            inline = _render_inline("\n".join(paragraph_lines))
            blocks.append(f"<p>{inline}</p>")
            paragraph_lines.clear()

    def flush_list() -> None:
        if list_items:
            rendered = "".join(f"<li>{_render_inline(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{rendered}</ul>")
            list_items.clear()

    def flush_ol_list() -> None:
        if ol_items:
            rendered = "".join(f"<li>{_render_inline(item)}</li>" for item in ol_items)
            start_attr = f' start="{ol_start[0]}"' if ol_start[0] != 1 else ""
            blocks.append(f"<ol{start_attr}>{rendered}</ol>")
            ol_items.clear()
            ol_start[0] = 1

    def flush_blockquote() -> None:
        if blockquote_lines:
            inline = _render_inline("\n".join(blockquote_lines))
            blocks.append(f"<blockquote>{inline}</blockquote>")
            blockquote_lines.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_ol_list()
        flush_blockquote()

    raw_lines = text.splitlines()
    i = 0
    while i < len(raw_lines):
        line = raw_lines[i]
        if line.startswith("```"):
            flush_all()
            if in_fence:
                content = "\n".join(fence_lines)
                if fence_lang == "mermaid":
                    # Mermaid graphs render client-side via mermaid.js;
                    # the diagram source is escaped so untrusted input
                    # cannot inject HTML.
                    blocks.append(f'<div class="mermaid">{html_escape(content)}</div>')
                else:
                    # Emit a `language-<lang>` class so highlight.js can
                    # auto-detect and colourise the block. Bare ``` fences
                    # (no lang) get no class — highlight.js falls back to
                    # auto-detection on those.
                    if fence_lang:
                        lang_attr = f' class="language-{html_escape(fence_lang)}"'
                    else:
                        lang_attr = ""
                    blocks.append(f"<pre><code{lang_attr}>{html_escape(content)}</code></pre>")
                fence_lines.clear()
                fence_lang = ""
                in_fence = False
            else:
                fence_lang = line[3:].strip().lower()
                in_fence = True
            i += 1
            continue
        if in_fence:
            fence_lines.append(line)
            i += 1
            continue
        if line.strip() == "":
            flush_all()
            i += 1
            continue
        # COD-079: GFM table — header row + delimiter row + body rows.
        # Tried before paragraphs / lists so leading `|` doesn't get
        # swallowed as plain text.
        table_render = _try_render_table(raw_lines, i)
        if table_render is not None:
            flush_all()
            html, end_idx = table_render
            blocks.append(html)
            i = end_idx
            continue
        # Heading? Slugify into id-attribute so anchor scroll works on TOC links.
        m = _HEADING.match(line)
        if m:
            flush_all()
            level = len(m.group(1))
            content = _render_inline(m.group(2))
            slug = _slugify(m.group(2))
            blocks.append(f'<h{level} id="{slug}">{content}</h{level}>')
            i += 1
            continue
        # Blockquote? Collapse consecutive `> …` lines into one <blockquote>.
        if line.startswith(">"):
            flush_paragraph()
            flush_list()
            flush_ol_list()
            blockquote_lines.append(line[1:].lstrip(" "))
            i += 1
            continue
        # Ordered list? (`1. item` or `1) item`)
        m_ol = _OL_ITEM.match(line)
        if m_ol:
            flush_paragraph()
            flush_blockquote()
            flush_list()
            if not ol_items:
                ol_start[0] = int(m_ol.group(1))
            ol_items.append(m_ol.group(2))
            i += 1
            continue
        # Bullet list?
        if line.startswith(("- ", "* ")):
            flush_paragraph()
            flush_blockquote()
            flush_ol_list()
            list_items.append(line[2:])
            i += 1
            continue
        flush_list()
        flush_ol_list()
        flush_blockquote()
        paragraph_lines.append(line)
        i += 1

    flush_all()
    if in_fence and fence_lines:
        # Unclosed fence — close gracefully so we never lose content.
        content = "\n".join(fence_lines)
        if fence_lang == "mermaid":
            blocks.append(f'<div class="mermaid">{html_escape(content)}</div>')
        else:
            if fence_lang:
                lang_attr = f' class="language-{html_escape(fence_lang)}"'
            else:
                lang_attr = ""
            blocks.append(f"<pre><code{lang_attr}>{html_escape(content)}</code></pre>")

    return "\n".join(blocks)


_SLUG_BAD = re.compile(r"[^\w\-]+", re.UNICODE)


def _slugify(text: str) -> str:
    """Compact heading → ASCII-ish id. Mostly for in-page anchor scroll."""
    s = text.strip().lower().replace(" ", "-")
    s = _SLUG_BAD.sub("", s)
    return s or "section"
