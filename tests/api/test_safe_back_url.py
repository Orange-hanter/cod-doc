"""``_safe_back_url``: возврат по ``Referer`` — только на свой сайт и без сырых байтов.

Итог всегда относительный путь, поэтому чужой хост — отказ, а свой хост в
любом написании (регистр, явный дефолтный порт) — тот же сайт. Путь и
запрос собираются заново с percent-кодированием: CR/LF, кавычки и угловые
скобки не доходят до ``Location`` сырыми, даже если бы их пропустил сервер.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from cod_doc.api.web.errors import _safe_back_url

_FALLBACK = "/p/demo"


def _request(referer: str | None, *, scheme: str = "http", host: str = "testserver") -> Request:
    headers = [(b"host", host.encode("latin-1"))]
    if referer is not None:
        headers.append((b"referer", referer.encode("latin-1")))
    port = 443 if scheme == "https" else 80
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": scheme,
            "server": (host.split(":")[0], port),
            "path": "/p/demo/tasks/X-1/complete",
            "query_string": b"",
            "headers": headers,
        }
    )


@pytest.mark.parametrize(
    ("referer", "expected"),
    [
        ("http://testserver/p/demo/plans/x?tab=a", "/p/demo/plans/x?tab=a"),
        ("http://TestServer/p/demo/x", "/p/demo/x"),
        ("HTTP://TESTSERVER/p/demo/x", "/p/demo/x"),
        ("http://testserver:80/p/demo/x", "/p/demo/x"),
        ("/p/demo/tasks", "/p/demo/tasks"),
        ("/p/demo/tasks?tab=ready&x=1", "/p/demo/tasks?tab=ready&x=1"),
        # Уже закодированное не кодируется повторно, сырое — кодируется.
        ("/p/demo?q=a%0d%0aSet-Cookie:x", "/p/demo?q=a%0d%0aSet-Cookie:x"),
        ('/p/demo?q=<script>"x"', "/p/demo?q=%3Cscript%3E%22x%22"),
        ("/p/demo/a b", "/p/demo/a%20b"),
    ],
)
def test_same_site_referer_becomes_relative_path(referer: str, expected: str) -> None:
    assert _safe_back_url(_request(referer), _FALLBACK) == expected


def test_https_default_port_matches() -> None:
    req = _request("https://testserver:443/p/demo/x", scheme="https")
    assert _safe_back_url(req, _FALLBACK) == "/p/demo/x"


@pytest.mark.parametrize(
    "referer",
    [
        None,
        "",
        "https://evil.example/phish",
        "http://testserver.evil.example/p/x",
        "http://testserver:8080/p/x",
        "https://testserver/p/x",
        "http://testserver:notaport/p/x",
        "//evil.example/phish",
        "///evil.example/phish",
        "javascript:alert(1)",
        "p/demo/relative-without-slash",
        # Обратный слеш в любом месте: браузеры читают его как «/».
        "/\\evil.example/phish",
        "/p/\\/evil",
        "\\\\evil.example",
        "http://testserver/p/x?next=\\evil",
        "/p/demo?a=\\b",
        # Управляющие символы — отказ целиком, а не чистка.
        "/p/demo?a=b\r\nSet-Cookie: x=1",
        "/p/demo/x\n",
        "/p/demo\tx",
        "/p/demo\x7f",
    ],
)
def test_unsafe_referer_falls_back(referer: str | None) -> None:
    assert _safe_back_url(_request(referer), _FALLBACK) == _FALLBACK
