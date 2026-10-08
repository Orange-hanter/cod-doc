"""Регулятор текста в Settings: страница его рендерит, стили его слушают.

Сам регулятор — клиентский (`cod_doc/static/cod_doc_text.js`, localStorage),
поэтому здесь проверяется контракт с вёрсткой: кегль, заданный пикселями
мимо `var(--text-scale)`, ползунком «Размер текста» не масштабируется, и
страница с ним выглядит рваной — часть текста растёт, часть нет.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2] / "cod_doc"
STYLED = [
    *sorted((ROOT / "static" / "css").glob("*.css")),
    *sorted((ROOT / "templates" / "web").rglob("*.html")),
]

# `font-size: 13px` и шорткат `font: 600 13px/1.4 …` — оба мимо регулятора.
_RAW_PX = re.compile(r"font-size:\s*\d+(?:\.\d+)?px|font:[^;{}\"]*?(?<!\()\b\d+(?:\.\d+)?px")


def test_every_font_size_goes_through_text_scale() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}"
        for path in STYLED
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _RAW_PX.search(line)
    ]
    assert not offenders, (
        "кегль в px без calc(Npx * var(--text-scale)) не масштабируется регулятором:\n"
        + "\n".join(offenders)
    )


def test_text_scale_token_has_default() -> None:
    base = (ROOT / "static" / "css" / "_base.css").read_text(encoding="utf-8")
    for token in (
        "--text-scale",
        "--line-height",
        "--font-prose",
        "--prose-line-height",
        "--prose-measure",
    ):
        assert re.search(rf"^\s*{token}:", base, re.MULTILINE), token


def test_settings_page_renders_text_style_section() -> None:
    from cod_doc.api.server import app

    with TestClient(app) as client:
        resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.text
    assert 'id="text-style"' in html
    # Скрипт стоит в <head> без defer: стиль должен лечь до отрисовки.
    head = html.split("</head>", 1)[0]
    assert re.search(r'<script src="/static/cod_doc_text\.js\?v=[^"]+"></script>', head)
    for key in ("scale", "lineHeight", "proseLineHeight", "measure", "sans", "prose", "mono"):
        assert f'data-key="{key}"' in html, key
