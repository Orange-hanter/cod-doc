"""Кит Ledger: каталог рендерится, у каждого класса кита есть правило.

Макросы — `cod_doc/templates/web/_ui/ledger.html`, стили —
`cod_doc/static/css/_ledger.css`. Тот же урок, что ADO-133 (классы `adr-*`
полгода жили без правил): класс `l-*` в шаблоне без правила в CSS — это
компонент, нарисованный браузерным дефолтом.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2] / "cod_doc"
# Первый сегмент — буквы: «L-2» из прозы шаблонов (уровни контекста) не класс.
_CLASS = re.compile(r"(?<![\w-])l-[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?![\w-])")


def _client() -> TestClient:
    from cod_doc.api.server import app

    return TestClient(app)


def test_kit_page_renders_every_section() -> None:
    with _client() as client:
        r = client.get("/ui/kit")
    assert r.status_code == 200
    body = r.text
    for marker in (
        'class="l-page-header"',
        "l-btn l-btn-primary",
        "l-tag l-tag-signal",
        'class="l-group"',
        "data-filter-group",
        "Показать ещё 5 из 8",
        "l-banner l-banner-warn",
        'class="l-empty"',
        "l-page-title-long",
    ):
        assert marker in body, marker
    # Длинный narrative режется по строкам, а не по символам: текст целиком в DOM.
    assert "сохранить переписку как доказательство для арбитража" in body


def test_ledger_is_default_palette() -> None:
    with _client() as client:
        r = client.get("/ui/kit")
    assert '<html lang="ru" data-theme="light" data-palette="ledger">' in r.text
    assert "css/_ledger.css" in r.text


def test_every_kit_class_in_templates_has_a_rule() -> None:
    css = (ROOT / "static" / "css" / "_ledger.css").read_text(encoding="utf-8")
    styled = set(_CLASS.findall(css))
    used: set[str] = set()
    for tpl in (ROOT / "templates" / "web").rglob("*.html"):
        used |= set(_CLASS.findall(tpl.read_text(encoding="utf-8")))
    unstyled = sorted(used - styled)
    assert not unstyled, f"классы кита без правила в _ledger.css: {unstyled}"


def test_tone_modifiers_are_styled() -> None:
    """Тоны собираются конкатенацией (`"l-tag-" ~ tone`) — сканер их не видит."""
    css = (ROOT / "static" / "css" / "_ledger.css").read_text(encoding="utf-8")
    for tone in ("signal", "warn", "info", "ok", "neutral"):
        assert f".l-tag-{tone}" in css
    for tone in ("signal", "warn", "info", "ok"):
        assert f".l-banner-{tone}" in css
    for kind in ("primary", "secondary", "signal", "ghost", "danger"):
        assert f".l-btn-{kind}" in css
