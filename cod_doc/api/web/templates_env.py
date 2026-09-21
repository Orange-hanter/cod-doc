"""Jinja2 environment for the web frontend."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final
from urllib.parse import unquote

from fastapi.templating import Jinja2Templates

from cod_doc.api.web.dates import fmt_date, fmt_datetime, fmt_relative, fmt_tooltip
from cod_doc.domain.entities import (
    TASK_STATUS_ALIASES,
    DocumentType,
    TaskStatus,
    canonical_task_status,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "web"
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

#: Порядок статусов в выпадающем списке — по жизненному циклу, а не по порядку
#: членов `TaskStatus`: в enum `DONE` исторически лежит в группе легаси-значений,
#: и список заканчивался бы `… cancelled, pending, in-progress, done`.
#: Значения остаются за enum — см. `_canonical_status_options`.
_STATUS_DISPLAY_ORDER: Final[tuple[str, ...]] = (
    "backlog",
    "todo",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "cancelled",
)


def _canonical_status_options() -> list[str]:
    """Канонические статусы задачи в порядке жизненного цикла.

    Легаси-написания (`TASK_STATUS_ALIASES`) в список не попадают (ADO-156):
    форма, предлагающая `pending` наравне с `todo`, позволяет человеку руками
    вернуть в БД ровно тот дрейф словаря, который вычищает бэкфилл.

    Канонический статус, которому не нашлось места в `_STATUS_DISPLAY_ORDER`,
    уезжает в хвост, но из списка не пропадает: новый член enum виден в форме
    сразу, а порядок поправит `tests/api/test_task_status_options.py`.
    """
    canonical = {s.value for s in TaskStatus} - set(TASK_STATUS_ALIASES)
    ordered = [value for value in _STATUS_DISPLAY_ORDER if value in canonical]
    ordered += sorted(canonical - set(ordered))
    return ordered


TASK_STATUS_OPTIONS: list[str] = _canonical_status_options()
DOCUMENT_TYPES: list[str] = [t.value for t in DocumentType]


def task_status_choices(current: str | None = None) -> list[tuple[str, str]]:
    """Пары `(value, label)` для `<select name="status">`.

    Канонические семь — плюс собственное написание задачи, если оно легаси:
    иначе `<select>` молча теряет выбранный пункт и показывает первый по
    списку. Так выглядит ещё не мигрированная БД или восстановленный бэкап,
    и врать о её содержимом форма не должна. Легаси помечено в видимой
    подписи, а не только в тултипе.
    """
    options = [(value, value) for value in TASK_STATUS_OPTIONS]
    if current and current not in TASK_STATUS_OPTIONS:
        label = f"{current} (legacy)" if current in TASK_STATUS_ALIASES else current
        options.append((current, label))
    return options


def chain_done_count(chain: dict[str, Any]) -> int:
    """Сколько задач плана закрыто — счётчик, которого не хватало в шапке цепочек.

    Считается по тем же строкам, что рисуют карточки (`levels`), чтобы шапка и
    полотно не могли разойтись. `Ready` в соседнем чипе — «готовы к старту», а
    не «закрыты», и без счётчика `done` рядом единственное число со смыслом
    статуса на всей строке читалось как «план не закрыт» (ADO-156).
    """
    return sum(
        1
        for level in chain.get("levels", ())
        for task in level.get("tasks", ())
        if canonical_task_status(str(task.get("status", ""))) == "done"
    )


# WEB-051: static asset cache-bust.
# Compute a short fingerprint from each file's mtime once at import; templates
# call `static_url("css/_components.css")` to get `/static/…?v=<hash>`.
# Browsers cache until the file changes; on bump → new query string → fresh fetch.
#
# Версионируется КАЖДЫЙ подключаемый файл, а не одна точка входа. Прежде
# `base.html` подключал `app.css`, который тянул партиалы через `@import`, и
# отпечаток точки входа считался с оглядкой на их mtime. Механика работала
# ровно как написана и цели всё равно не достигала: импорты внутри CSS идут
# без версии, поэтому браузер перекачивал `app.css` и брал `_components.css`
# из кэша. Проверено живьём — `app.css?v=` был новый, а применялся старый
# `_components.css`. Лечится только версией на самом файле.
_STATIC_VERSION_CACHE: dict[str, str] = {}


def _fingerprint(name: str) -> str:
    """Hex-formatted mtime of the static file. Empty string if missing."""
    if name not in _STATIC_VERSION_CACHE:
        path = STATIC_DIR / name
        try:
            mtime = path.stat().st_mtime
        except OSError:
            _STATIC_VERSION_CACHE[name] = ""
        else:
            # 8 hex chars of mtime are enough to bust browser cache.
            _STATIC_VERSION_CACHE[name] = f"{int(mtime):x}"[-8:]
    return _STATIC_VERSION_CACHE[name]


def static_url(name: str) -> str:
    """Return /static/<name>?v=<fingerprint> for cache-bust on file change."""
    fp = _fingerprint(name)
    return f"/static/{name}?v={fp}" if fp else f"/static/{name}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# `urldecode` lets base.html surface percent-encoded flash cookies (which we
# encode at write time because cookie headers are latin-1).
templates.env.filters["urldecode"] = unquote

# ADO-154: единственная точка форматирования времени — cod_doc/api/web/dates.py.
# Правило: хендлер отдаёт доменное значение как есть, шаблон выбирает форму.
# `.isoformat()` в pages/ законен только при сериализации (JSONResponse или
# JSON-файл), но не для контекста шаблона.
templates.env.filters["relative_time"] = fmt_relative
templates.env.filters["short_datetime"] = fmt_datetime
templates.env.filters["short_date"] = fmt_date
templates.env.filters["ts_tooltip"] = fmt_tooltip
# Avoid passing the same enum dump from every handler — make it a Jinja global.
templates.env.globals["task_status_options"] = TASK_STATUS_OPTIONS
templates.env.globals["task_status_choices"] = task_status_choices
templates.env.globals["document_types"] = DOCUMENT_TYPES
templates.env.filters["chain_done_count"] = chain_done_count
templates.env.globals["static_url"] = static_url
# COD-077 (d): operators behind a corp firewall can point at a vendored
# Mermaid build (e.g. /static/mermaid.esm.min.mjs); default = jsDelivr CDN.
templates.env.globals["mermaid_src"] = os.environ.get(
    "COD_DOC_MERMAID_SRC",
    "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs",
)

# highlight.js for syntax-highlighted code blocks in rendered markdown.
# Operators behind a firewall can vendor the assets and point the env vars
# at /static/<file> — defaults are CDNs.
templates.env.globals["hljs_src"] = os.environ.get(
    "COD_DOC_HLJS_SRC",
    "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.10.0/build/highlight.min.js",
)
templates.env.globals["hljs_light_css"] = os.environ.get(
    "COD_DOC_HLJS_LIGHT_CSS",
    "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.10.0/build/styles/github.min.css",
)
templates.env.globals["hljs_dark_css"] = os.environ.get(
    "COD_DOC_HLJS_DARK_CSS",
    "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.10.0/build/styles/github-dark.min.css",
)
