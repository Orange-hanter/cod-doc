"""Панель «Связи»: в href попадает только http(s) (REL-012, защита от XSS).

`relate` отвергает не-http(s) url, но строка может прийти синком или из старых
таблиц мимо этой проверки — рендер не должен ей доверять.
"""

from __future__ import annotations

import pytest

from cod_doc.api.web.relations import href


@pytest.mark.parametrize(
    "ref",
    [
        "url:javascript:alert(1)",
        "url:JavaScript:alert(1)",
        "url:data:text/html,<script>alert(1)</script>",
        "url:vbscript:msgbox(1)",
        "url://evil.example/x",
    ],
)
def test_non_http_url_gets_no_href(ref: str) -> None:
    assert href("p", ref) == ""


@pytest.mark.parametrize("ref", ["url:https://example.com/a", "url:HTTP://example.com"])
def test_http_url_is_linked(ref: str) -> None:
    assert href("p", ref) == ref.removeprefix("url:")
