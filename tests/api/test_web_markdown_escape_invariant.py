"""ADO-111/ADO-112: escape-first остаётся доказуемым после двух новых веток.

Модель безопасности рендерера — не санитайзер, а порядок: `html.escape`
отрабатывает до любого markdown-регекса, поэтому сырой HTML не может
просочиться. Обе новые фичи структурно безопасны — стрип комментариев только
**удаляет** символы из сырого текста, ветка `<hr>` вставляет литерал без
интерполяции, — но «структурно безопасно» проверяется, а не обещается.

Отличие от точечных security-кейсов в test_web_markdown.py (те проверяют
конкретные конструкции): здесь враждебный корпус прогоняется целиком и
утверждается, что в выводе не осталось НИ ОДНОГО `<` вне разрешённого
списка тегов. Новая ветка рендерера, забывшая про escape, упадёт здесь,
даже если её автор не подумал написать свой security-тест.
"""

from __future__ import annotations

import re

import pytest

from cod_doc.api.web.markdown import render_markdown

#: Теги, которые рендерер имеет право выпускать.
_ALLOWED = (
    "p",
    "strong",
    "em",
    "a",
    "code",
    "pre",
    "ul",
    "ol",
    "li",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "div",
    "aside",
    "hr",
)
_TAG_RE = re.compile(r"</?(?:" + "|".join(_ALLOWED) + r")\b[^>]*/?>")

HOSTILE = [
    "<!-- <script>alert(1)</script> -->",
    "<!--\n<script>alert(2)</script>\nвторая строка\n-->",
    "<!-- не закрыт <script>alert(3)</script>",
    "<!-- @REVIEW: <script>alert(4)</script> -->",
    "<!-- @REVIEW: <img src=x onerror=alert(5)> -->",
    "--- <script>alert(6)</script>",
    "<!-- a --><script>alert(7)</script>",
    "```\n<!-- <script>alert(8)</script> -->\n```",
    "*** <img onerror=1>",
    "текст <!-- x --> <b>жирный</b> хвост",
    "<!--\n# <script>alert(9)</script>\n-->",
    "---\n<script>alert(10)</script>\n---",
]


@pytest.mark.parametrize("src", HOSTILE)
def test_rendered_html_contains_only_whitelisted_tags(src: str) -> None:
    residue = _TAG_RE.sub("", render_markdown(src))
    assert "<" not in residue, f"незаэкранированный `<` в выводе для {src!r}: {residue!r}"


@pytest.mark.parametrize("src", HOSTILE)
def test_no_script_tag_survives(src: str) -> None:
    out = render_markdown(src).lower()
    assert "<script" not in out
    assert "onerror=" not in out or "&lt;img" in out or "&amp;" in out


def test_the_invariant_would_catch_a_naive_renderer() -> None:
    """Страховка от вечнозелёного теста: `_TAG_RE` не должен съедать всё подряд."""
    residue = _TAG_RE.sub("", "<p>ok</p><script>bad</script>")
    assert "<script>" in residue, "белый список слишком широк — тест ничего не проверяет"
