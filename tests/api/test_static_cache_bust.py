"""ADO-144: правка CSS-партиала обязана сбрасывать кэш браузера.

Первая редакция лечила симптом на слой выше, чем нужно. Разметка подключала
одну точку входа `app.css`, а та тянула три реальных файла через
``@import url("css/…")``. Отпечаток точки входа считался с оглядкой на mtime
импортированных файлов — и механика работала ровно так, как написана: правка
`_components.css` меняла `app.css?v=`.

Цели это не достигало. Импорты **внутри** CSS идут без версии, поэтому браузер
перекачивал `app.css`, видел прежний `url("css/_components.css")` и брал его из
своего кэша. Поймано в браузере: `app.css?v=` был новый, а применялся старый
партиал — `min-width` свежей правки не действовал, `getComputedStyle` показывал
`0px`.

Тесты той редакции были зелёными, потому что проверяли механику отпечатка, а не
её цель. Здесь проверяется цель: у каждого подключённого файла своя версия, и
правка файла эту версию меняет.
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

#: Заведомо различимые метки времени: разница в 10 000 секунд видна в
#: младших разрядах hex-отпечатка.
_T0 = 1_700_000_000
_T1 = _T0 + 10_000

#: Партиалы, которые подключает `base.html`. Список продублирован намеренно:
#: расхождение с шаблоном ловит `test_base_html_links_every_partial`.
_PARTIALS = ("css/_base.css", "css/_components.css", "css/_task_detail.css")


@pytest.fixture(autouse=True)
def clean_cache() -> Iterator[None]:
    """Отпечатки кэшируются на процесс; между кейсами кэш сбрасываем."""
    env._STATIC_VERSION_CACHE.clear()
    yield
    env._STATIC_VERSION_CACHE.clear()


@pytest.fixture
def fake_static(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Свой каталог статики вместо настоящего `cod_doc/static`.

    На реальных файлах тест был бы проверкой состояния чекаута: в свежем клоне
    все они выложены одной секундой, и отличить «учли mtime» от «не учли»
    нельзя.
    """
    static = tmp_path / "static"
    (static / "css").mkdir(parents=True)
    for rel in _PARTIALS:
        path = static / rel
        path.write_text(".x{}", encoding="utf-8")
        os.utime(path, (_T0, _T0))
    monkeypatch.setattr(env, "STATIC_DIR", static)
    return static


def test_edit_of_a_partial_changes_its_own_version(fake_static: Path) -> None:
    """Правка файла меняет версию именно этого файла."""
    old = env.static_url("css/_components.css")

    os.utime(fake_static / "css" / "_components.css", (_T1, _T1))
    env._STATIC_VERSION_CACHE.clear()
    new = env.static_url("css/_components.css")

    assert old != new, (
        "правка css/_components.css не изменила ?v= — "
        "у вернувшегося посетителя останется старый CSS"
    )
    assert re.fullmatch(r"/static/css/_components\.css\?v=[0-9a-f]{1,8}", new), new


def test_edit_of_a_partial_leaves_the_others_alone(fake_static: Path) -> None:
    """Обратная сторона: соседи не перекачиваются из-за чужой правки.

    Ровно это теряла прежняя схема с одной точкой входа — правка любого файла
    меняла общую версию, и браузер шёл за всеми тремя.
    """
    before = env.static_url("css/_base.css")

    os.utime(fake_static / "css" / "_components.css", (_T1, _T1))
    env._STATIC_VERSION_CACHE.clear()

    assert env.static_url("css/_base.css") == before


def test_fingerprint_is_stable_without_changes(fake_static: Path) -> None:
    """Без правок отпечаток не скачет.

    Без этого кейса предыдущие прошли бы и на реализации, которая возвращает
    случайное значение — кэш сбрасывался бы на каждый запрос.
    """
    first = env.static_url("css/_components.css")
    env._STATIC_VERSION_CACHE.clear()
    assert env.static_url("css/_components.css") == first


def test_missing_file_has_no_version() -> None:
    """Отсутствующий файл отдаётся без ?v=, а не с пустым значением."""
    assert env.static_url("no-such-file.css") == "/static/no-such-file.css"
