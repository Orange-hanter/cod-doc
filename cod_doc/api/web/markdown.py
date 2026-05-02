"""Tiny safe-by-default markdown renderer for section bodies (WEB-006).

Scope is intentionally minimal — the markdown that appears in
`Section.body` and `Document.preamble` is:
- short
- written by the user (not LLM hallucination), so we trust intent
- rarely uses tables / nested lists / footnotes

What we render:
- Paragraphs separated by blank lines (`<p>`)
- Code fences ``` ... ``` (```<pre><code>```, language hint discarded)
- Bullet lists with `- ` or `* ` markers
- Inline: `code`, **bold**, *italic*, [text](url)

What we explicitly do NOT render:
- Tables, footnotes, blockquotes, images, nested lists
- Headings inside section bodies (the section heading is rendered by the
  caller — `<h{level}>{section.heading}</h{level}>` outside this function)

Safety: input is HTML-escaped before any markdown patterns run, so user
input cannot smuggle raw HTML. URLs in `[text](url)` are escaped but not
filtered — same trust assumption as everything else in `cod_doc/services`.

Adopted instead of pulling in `markdown-it-py` (~50 KB + transitive deps)
to honor capability §2 «Никаких новых зависимостей сверх уже имеющихся».
Re-evaluate the trade-off if doc bodies grow markdown features beyond the
inline four.
"""

from __future__ import annotations

import re
from html import escape as html_escape

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _shield_inline_code(match: re.Match[str]) -> str:
    """Replace markdown-active chars inside `code` with HTML entities so
    later regex passes don't peek inside.
    """
    content = match.group(1)
    for ch, entity in (("*", "&#42;"), ("_", "&#95;"), ("[", "&#91;"), ("`", "&#96;")):
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
    fence_lines: list[str] = []
    paragraph_lines: list[str] = []
    list_items: list[str] = []

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

    for line in text.splitlines():
        if line.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_fence:
                code_html = html_escape("\n".join(fence_lines))
                blocks.append(f"<pre><code>{code_html}</code></pre>")
                fence_lines.clear()
                in_fence = False
            else:
                in_fence = True
            continue
        if in_fence:
            fence_lines.append(line)
            continue
        if line.strip() == "":
            flush_paragraph()
            flush_list()
            continue
        if line.startswith(("- ", "* ")):
            flush_paragraph()
            list_items.append(line[2:])
            continue
        flush_list()
        paragraph_lines.append(line)

    flush_paragraph()
    flush_list()
    if in_fence and fence_lines:
        # Unclosed fence — close gracefully so we never lose content.
        code_html = html_escape("\n".join(fence_lines))
        blocks.append(f"<pre><code>{code_html}</code></pre>")

    return "\n".join(blocks)
