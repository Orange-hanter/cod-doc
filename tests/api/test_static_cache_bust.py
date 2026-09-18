"""ADO-144: правка CSS-партиала обязана сбрасывать кэш браузера.

В разметке версионируется ровно один файл — `static_url('app.css')` даёт
`/static/app.css?v=<mtime>`. Но сам `app.css` — только точка входа: три
реальных файла стилей он тянет через `@import url("css/…")`, и у этих
запросов версии нет. Пока отпечаток считался по mtime одного `app.css`,
правка `css/_components.css` не меняла в разметке ни байта: вернувшийся
посетитель получал старый CSS до ручного hard-reload.

Поймано не тестом, а показом страницы в браузере после фикса вёрстки
ADO-144 — первая навигация отдала прежние стили. Поэтому тест здесь
проверяет ровно наблюдаемое следствие («отпечаток изменился»), а не
внутреннее устройство функции.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import pytest

from cod_doc.api.web import templates_env as env

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CSS_ENTRY = "app.css"


@pytest.fixture
def clean_cache() -> Iterator[None]:
    """Отпечатки кэшируются на процесс; между кейсами кэш сбрасываем."""
    env._STATIC_VERSION_CACHE.clear()
    yield
    env._STATIC_VERSION_CACHE.clear()


def _touch(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def test_app_css_declares_imports() -> None:
    """Предпосылка теста: без @import проверять нечего."""
    text = (env.STATIC_DIR / CSS_ENTRY).read_text(encoding="utf-8")
    assert env._CSS_IMPORT_RE.findall(text), "app.css больше не тянет партиалы — тест устарел"


def test_fingerprint_covers_imported_partials(clean_cache: None) -> None:
    """Правка партиала меняет отпечаток точки входа."""
    partial = env.STATIC_DIR / "css" / "_components.css"
    entry = env.STATIC_DIR / CSS_ENTRY
    before_partial = partial.stat().st_mtime
    before_entry = entry.stat().st_mtime

    try:
        _touch(partial, before_partial - 10_000)
        _touch(entry, before_entry - 10_000)
        env._STATIC_VERSION_CACHE.clear()
        old = env.static_url(CSS_ENTRY)

        # Трогаем ТОЛЬКО партиал: mtime самой точки входа не меняется.
        _touch(partial, before_partial)
        env._STATIC_VERSION_CACHE.clear()
        new = env.static_url(CSS_ENTRY)
    finally:
        _touch(partial, before_partial)
        _touch(entry, before_entry)
        env._STATIC_VERSION_CACHE.clear()

    assert old != new, (
        "правка css/_components.css не изменила ?v= у app.css — "
        "у вернувшегося посетителя останется старый CSS"
    )
    assert re.fullmatch(r"/static/app\.css\?v=[0-9a-f]{1,8}", new), new


def test_missing_file_has_no_version(clean_cache: None) -> None:
    """Отсутствующий файл отдаётся без ?v=, а не с пустым значением."""
    assert env.static_url("no-such-file.css") == "/static/no-such-file.css"


def test_remote_imports_are_ignored(tmp_path: Path) -> None:
    """`@import url(https://…)` — не локальный путь, stat по нему не зовём."""
    css = tmp_path / "entry.css"
    css.write_text(
        '@import url("https://fonts.example/x.css");\n@import url("local.css");\n',
        encoding="utf-8",
    )
    (tmp_path / "local.css").write_text("body{}", encoding="utf-8")
    assert env._imported_paths(css) == [(tmp_path / "local.css").resolve()]


def test_non_css_has_no_imports(tmp_path: Path) -> None:
    js = tmp_path / "x.js"
    js.write_text('// @import url("nope.css")', encoding="utf-8")
    assert env._imported_paths(js) == []
