"""ADO-199: advisory PS-004 на HTML-сущности в тексте (`audit_html_escaped_text`).

Эталоны — литералы: ожидание, построенное той же регуляркой, что и проверка,
совпало бы с ней и на её собственной ошибке.
"""

from __future__ import annotations

from typing import Any

import pytest

from cod_doc.services.validation import audit_html_escaped_text


@pytest.mark.parametrize(
    "text",
    ["R&amp;D", "a &lt; b", "say &quot;hi&quot;", "x &#38; y", "x &#x26; y"],
)
def test_each_entity_warns(text: str) -> None:
    issues = audit_html_escaped_text(text)
    assert len(issues) == 1
    assert issues[0].code == "PS-004"
    assert issues[0].severity == "warning"


@pytest.mark.parametrize(
    ("text", "entities"),
    [
        ("a &gt; b", ["&gt;"]),
        ("it&apos;s", ["&apos;"]),
        ("x &#X2F; y", ["&#X2F;"]),
    ],
)
def test_other_entities_detected(text: str, entities: list[str]) -> None:
    issues = audit_html_escaped_text(text)
    assert len(issues) == 1
    assert issues[0].details["entities"] == entities


def test_entities_listed_once_in_order() -> None:
    issues = audit_html_escaped_text("a &amp; b &lt; c &amp; d")
    assert len(issues) == 1
    assert issues[0].details["entities"] == ["&amp;", "&lt;"]
    assert issues[0].details["text"] == "a &amp; b &lt; c &amp; d"


@pytest.mark.parametrize("text", ["R&D", "a < b", "&", "AT&T", ""])
def test_clean_text_no_issues(text: str) -> None:
    assert audit_html_escaped_text(text) == []


@pytest.mark.parametrize("text", [None, 123, "a\x00b\x1f\x7fc&"])
def test_never_raises(text: Any) -> None:
    assert audit_html_escaped_text(text) == []


def test_field_in_message() -> None:
    issues = audit_html_escaped_text("R&amp;D", field="title")
    assert len(issues) == 1
    assert "title" in issues[0].message
    assert issues[0].details["field"] == "title"
