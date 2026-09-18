"""ADO-144: правка CSS-партиала обязана сбрасывать кэш браузера.

В разметке версионируется ровно один файл — `static_url('app.css')` даёт
`/static/app.css?v=<mtime>`. Но сам `app.css` — только точка входа: три
реальных файла стилей он тянет через `@import url("css/…")`, и у этих
запросов версии нет. Пока отпечаток считался по mtime одного `app.css`,
правка `css/_components.css` не меняла в разметке ни байта: вернувшийся
посетитель получал старый CSS до ручного hard-reload.

Поймано не тестом, а показом страницы в браузере после фикса вёрстки
ADO-144 — первая навигация отдала прежние стили.

Проверка идёт на СВОЁМ каталоге статики в `tmp_path`, а не на настоящем
`cod_doc/static`. Первая версия теста трогала mtime реальных файлов и
была зелёной локально, но красной в CI: в свежем клоне все файлы выложены
одной секундой, поэтому нетронутые соседние партиалы (`_base.css`,
`_task_detail.css`) перебивали максимум, и оба отпечатка совпадали.
Локально тест «работал» лишь потому, что в рабочем дереве
`_components.css` действительно был самым свежим — то есть проверял
состояние чекаута, а не поведение кода.
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

#: Заведомо различимые метки времени: разница в 10 000 секунд видна в
#: младших разрядах hex-отпечатка.
_T0 = 1_700_000_000
_T1 = _T0 + 10_000


@pytest.fixture(autouse=True)
def clean_cache() -> Iterator[None]:
    """Отпечатки кэшируются на процесс; между кейсами кэш сбрасываем."""
    env._STATIC_VERSION_CACHE.clear()
    yield
    env._STATIC_VERSION_CACHE.clear()


@pytest.fixture
def fake_static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Свой каталог статики: точка входа плюс два партиала.

    Два, а не один: с одним тест прошёл бы и на реализации, которая берёт
    mtime первого попавшегося импорта.
    """
    static = tmp_path / "static"
    (static / "css").mkdir(parents=True)
    (static / CSS_ENTRY).write_text(
        '@import url("css/_base.css");\n@import url("css/_components.css");\n',
        encoding="utf-8",
    )
    (static / "css" / "_base.css").write_text(":root{}", encoding="utf-8")
    (static / "css" / "_components.css").write_text(".x{}", encoding="utf-8")

    for path in (
        static / CSS_ENTRY,
        static / "css" / "_base.css",
        static / "css" / "_components.css",
    ):
        os.utime(path, (_T0, _T0))

    monkeypatch.setattr(env, "STATIC_DIR", static)
    return static


def test_app_css_declares_imports() -> None:
    """Предпосылка всей затеи, проверяется на НАСТОЯЩЕМ app.css.

    Если точка входа перестанет тянуть партиалы через ``@import``, механика
    отпечатка по импортам станет мёртвым кодом, а тесты на `fake_static`
    продолжат зеленеть на выдуманной структуре.
    """
    text = (env.STATIC_DIR / CSS_ENTRY).read_text(encoding="utf-8")
    imports = env._CSS_IMPORT_RE.findall(text)
    assert imports, "app.css больше не тянет партиалы — механика отпечатка не нужна"
    assert any("_components.css" in ref for ref in imports), imports


def test_fingerprint_covers_imported_partials(fake_static: Path) -> None:
    """Правка партиала меняет отпечаток точки входа.

    Точка входа и второй партиал остаются на `_T0`: меняется ровно один
    импортированный файл, как при обычной правке стилей.
    """
    old = env.static_url(CSS_ENTRY)

    os.utime(fake_static / "css" / "_components.css", (_T1, _T1))
    env._STATIC_VERSION_CACHE.clear()
    new = env.static_url(CSS_ENTRY)

    assert old != new, (
        "правка css/_components.css не изменила ?v= у app.css — "
        "у вернувшегося посетителя останется старый CSS"
    )
    assert re.fullmatch(r"/static/app\.css\?v=[0-9a-f]{1,8}", new), new


def test_fingerprint_is_stable_without_changes(fake_static: Path) -> None:
    """Обратная сторона: без правок отпечаток не скачет.

    Без этого кейса предыдущий тест прошёл бы и на реализации, которая
    возвращает случайное значение — кэш сбрасывался бы на каждый запрос.
    """
    first = env.static_url(CSS_ENTRY)
    env._STATIC_VERSION_CACHE.clear()
    assert env.static_url(CSS_ENTRY) == first


def test_entry_mtime_still_counts(fake_static: Path) -> None:
    """Правка самой точки входа тоже обязана менять отпечаток."""
    old = env.static_url(CSS_ENTRY)

    os.utime(fake_static / CSS_ENTRY, (_T1, _T1))
    env._STATIC_VERSION_CACHE.clear()
    assert env.static_url(CSS_ENTRY) != old


def test_missing_file_has_no_version() -> None:
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


def test_missing_import_target_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Опечатка в `@import` не должна ронять рендер страницы."""
    static = tmp_path / "static"
    static.mkdir()
    entry = static / CSS_ENTRY
    entry.write_text('@import url("css/_gone.css");\n', encoding="utf-8")
    os.utime(entry, (_T0, _T0))
    monkeypatch.setattr(env, "STATIC_DIR", static)

    assert re.fullmatch(r"/static/app\.css\?v=[0-9a-f]{1,8}", env.static_url(CSS_ENTRY))


def test_non_css_has_no_imports(tmp_path: Path) -> None:
    js = tmp_path / "x.js"
    js.write_text('// @import url("nope.css")', encoding="utf-8")
    assert env._imported_paths(js) == []
