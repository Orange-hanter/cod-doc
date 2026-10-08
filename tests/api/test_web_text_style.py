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
CSS = sorted((ROOT / "static" / "css").glob("*.css"))
TEMPLATES = sorted((ROOT / "templates" / "web").rglob("*.html"))
TEXT_JS = (ROOT / "static" / "cod_doc_text.js").read_text(encoding="utf-8")
BASE_CSS = (ROOT / "static" / "css" / "_base.css").read_text(encoding="utf-8")

# `font-size: 13px` и шорткат `font: 600 13px/1.4 …` — оба мимо регулятора.
_RAW_PX = re.compile(r"font-size:\s*\d+(?:\.\d+)?px|font:[^;{}\"]*?(?<!\()\b\d+(?:\.\d+)?px")
# В шаблоне стилем считается только то, что браузер применит: блок <style>
# и атрибут style="…". Пример `font-size: 14px` в прозе или {# … #} — не он.
_STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style>", re.DOTALL)
_STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _styles(path: Path) -> list[tuple[int, str]]:
    """(номер строки, CSS-текст) — весь файл для .css, стили для шаблона."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".css":
        return list(enumerate(text.splitlines(), 1))
    chunks: list[tuple[int, str]] = []
    for match in _STYLE_BLOCK.finditer(text):
        first = text.count("\n", 0, match.start(1)) + 1
        chunks += [(first + i, line) for i, line in enumerate(match.group(1).splitlines())]
    for match in _STYLE_ATTR.finditer(text):
        chunks.append((text.count("\n", 0, match.start()) + 1, match.group(1) or match.group(2)))
    return chunks


def test_every_font_size_goes_through_text_scale() -> None:
    offenders = [
        f"{path.relative_to(ROOT)}:{lineno}: {css.strip()}"
        for path in (*CSS, *TEMPLATES)
        for lineno, css in _styles(path)
        if _RAW_PX.search(css)
    ]
    assert not offenders, (
        "кегль в px без calc(Npx * var(--text-scale, 1)) не масштабируется регулятором:\n"
        + "\n".join(offenders)
    )


def test_style_scan_ignores_prose_and_sees_inline_styles(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(
        "{# пример: font-size: 14px #}\n<p>font-size: 14px</p>\n"
        '<div style="font-size:13px">x</div>\n<style>\n  b { font: 600 12px/1 x; }\n</style>\n',
        encoding="utf-8",
    )
    hits = [lineno for lineno, css in _styles(page) if _RAW_PX.search(css)]
    assert hits == [5, 3]


def _root_tokens() -> dict[str, str]:
    block = re.search(r"^:root\s*\{(.*?)^\}", BASE_CSS, re.DOTALL | re.MULTILINE)
    assert block, ":root не найден в _base.css"
    return dict(re.findall(r"^\s*(--[\w-]+):\s*([^;]+);", block.group(1), re.MULTILINE))


def _standard_values() -> dict[str, str]:
    block = re.search(r"standard:\s*\{.*?values:\s*\{([^}]*)\}", TEXT_JS, re.DOTALL)
    assert block, "PRESETS.standard.values не найден в cod_doc_text.js"
    return {k: v.strip().strip("'") for k, v in re.findall(r"(\w+):\s*([^,]+)", block.group(1))}


def _font_stack(group: str, font_id: str) -> str:
    section = re.search(
        rf"^\s{{4}}{group}:\s*\{{(.*?)^\s{{4}}\}},", TEXT_JS, re.DOTALL | re.MULTILINE
    )
    assert section, f"FONTS.{group} не найден"
    stack = re.search(
        rf"^\s*{font_id}:\s*\{{.*?stack:\s*('([^']*)'|null)", section.group(1), re.MULTILINE
    )
    assert stack, f"FONTS.{group}.{font_id} не найден"
    return stack.group(2) if stack.group(2) is not None else "null"


def test_standard_preset_matches_root_defaults() -> None:
    """apply() не пишет инлайн для значений «Стандарта», полагаясь на :root.

    Разъедутся — сохранённый «Стандарт» молча покажет CSS-значение вместо
    своего, а ползунок будет стоять не там, где текст.
    """
    root = _root_tokens()
    std = _standard_values()
    assert float(root["--text-scale"]) == float(std["scale"]) / 100
    assert float(root["--line-height"]) == float(std["lineHeight"])
    assert float(root["--prose-line-height"]) == float(std["proseLineHeight"])
    assert (root["--prose-measure"] == "none") == (float(std["measure"]) == 0)
    assert root["--font-sans"] == _font_stack("sans", std["sans"])
    assert root["--font-mono"] == _font_stack("mono", std["mono"])
    assert _font_stack("prose", std["prose"]) == "null"
    assert root["--font-prose"] == "var(--font-sans)"


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
    # Каркас для codDocText.mount(): контрол на каждое поле стиля, ключи
    # берутся из самого модуля — переименование там роняет тест здесь.
    section = html.split('id="text-style"', 1)[1]
    assert "data-ts-presets" in section
    assert "data-ts-reset" in section
    rendered = set(re.findall(r'data-key="(\w+)"', section))
    assert rendered == set(_standard_values())
    for key in ("scale", "lineHeight", "proseLineHeight", "measure"):
        assert f'data-ts-out="{key}"' in section, key
