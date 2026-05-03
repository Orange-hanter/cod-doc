r"""Tiny safe-by-default markdown renderer (WEB-006 + heading/blockquote).

Scope grew to cover MASTER.md preview on the project overview, in addition
to section bodies. Block-level features:
- ATX headings `# … ######`
- Bullet lists (`- ` / `* `)
- Code fences ``` … ``` (language tag discarded; preserves whitespace)
- Blockquotes `> …` (collapsible consecutive lines)
- Paragraphs separated by blank lines

Inline features (escaped first, then matched):
- `code`, **bold**, *italic*, [text](url)
- Markdown-active chars inside backticks are entity-shielded so `*foo*`
  inside `\`code\`` doesn't become italics inside <code>.

What we explicitly DO NOT render: tables, footnotes, images, nested
lists. Adopted instead of pulling in `markdown-it-py` (~50 KB + transitive
deps) to honor capability §2 («no new deps without justification»). When
section bodies start needing tables, revisit the ADR.

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

    def flush_blockquote() -> None:
        if blockquote_lines:
            inline = _render_inline("\n".join(blockquote_lines))
            blocks.append(f"<blockquote>{inline}</blockquote>")
            blockquote_lines.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_blockquote()

    for line in text.splitlines():
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
                    blocks.append(f"<pre><code>{html_escape(content)}</code></pre>")
                fence_lines.clear()
                fence_lang = ""
                in_fence = False
            else:
                fence_lang = line[3:].strip().lower()
                in_fence = True
            continue
        if in_fence:
            fence_lines.append(line)
            continue
        if line.strip() == "":
            flush_all()
            continue
        # Heading? Slugify into id-attribute so anchor scroll works on TOC links.
        m = _HEADING.match(line)
        if m:
            flush_all()
            level = len(m.group(1))
            content = _render_inline(m.group(2))
            slug = _slugify(m.group(2))
            blocks.append(f'<h{level} id="{slug}">{content}</h{level}>')
            continue
        # Blockquote? Collapse consecutive `> …` lines into one <blockquote>.
        if line.startswith(">"):
            flush_paragraph()
            flush_list()
            blockquote_lines.append(line[1:].lstrip(" "))
            continue
        # Bullet list?
        if line.startswith(("- ", "* ")):
            flush_paragraph()
            flush_blockquote()
            list_items.append(line[2:])
            continue
        flush_list()
        flush_blockquote()
        paragraph_lines.append(line)

    flush_all()
    if in_fence and fence_lines:
        # Unclosed fence — close gracefully so we never lose content.
        content = "\n".join(fence_lines)
        if fence_lang == "mermaid":
            blocks.append(f'<div class="mermaid">{html_escape(content)}</div>')
        else:
            blocks.append(f"<pre><code>{html_escape(content)}</code></pre>")

    return "\n".join(blocks)


_SLUG_BAD = re.compile(r"[^\w\-]+", re.UNICODE)


def _slugify(text: str) -> str:
    """Compact heading → ASCII-ish id. Mostly for in-page anchor scroll."""
    s = text.strip().lower().replace(" ", "-")
    s = _SLUG_BAD.sub("", s)
    return s or "section"
