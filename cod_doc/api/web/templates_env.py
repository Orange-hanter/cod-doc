"""Jinja2 environment for the web frontend."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

from fastapi.templating import Jinja2Templates

from cod_doc.api.web.dates import fmt_date, fmt_datetime, fmt_relative, fmt_tooltip
from cod_doc.domain.entities import DocumentType, TaskStatus

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "web"
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

TASK_STATUS_OPTIONS: list[str] = [s.value for s in TaskStatus]
DOCUMENT_TYPES: list[str] = [t.value for t in DocumentType]


# WEB-051: static asset cache-bust.
# Compute a short fingerprint from each file's mtime once at import; templates
# call `static_url("app.css")` to get `/static/app.css?v=<hash>`. Browsers cache
# until the file changes; on bump → new query string → fresh fetch.
_STATIC_VERSION_CACHE: dict[str, str] = {}


#: `@import url("css/_base.css")` внутри CSS-точки входа. Кавычки
#: необязательны, пробелы вокруг — тоже.
_CSS_IMPORT_RE = re.compile(r"""@import\s+url\(\s*['"]?([^'")]+)['"]?\s*\)""")


def _imported_paths(path: Path) -> list[Path]:
    """Локальные файлы, которые CSS тянет через ``@import`` (один уровень).

    Один уровень — не упрощение, а факт: партиалы в ``static/css/`` ничего
    не импортируют сами. Появится вложенный импорт — его mtime перестанет
    учитываться, и это заметит `test_app_css_fingerprint_covers_partials`.
    """
    if path.suffix != ".css":
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[Path] = []
    for ref in _CSS_IMPORT_RE.findall(text):
        if ref.startswith(("http://", "https://", "//", "data:")):
            continue
        out.append((path.parent / ref).resolve())
    return out


def _fingerprint(name: str) -> str:
    """Hex-formatted mtime of the static file. Empty string if missing.

    Для CSS учитывается и mtime партиалов, которые файл тянет через
    ``@import``. Без этого правка `css/_components.css` не сбрасывала кэш
    браузера ничем: в разметке версионируется только `app.css`, а его
    собственный mtime при правке партиала не меняется, и у вернувшегося
    посетителя оставался старый CSS до ручного hard-reload. Поймано
    показом страницы в браузере после фикса вёрстки ADO-144: первая
    навигация отдала старый стиль.
    """
    if name not in _STATIC_VERSION_CACHE:
        path = STATIC_DIR / name
        try:
            stamps = [path.stat().st_mtime]
        except OSError:
            _STATIC_VERSION_CACHE[name] = ""
        else:
            for dep in _imported_paths(path):
                try:
                    stamps.append(dep.stat().st_mtime)
                except OSError:
                    continue
            # 8 hex chars of mtime are enough to bust browser cache.
            _STATIC_VERSION_CACHE[name] = f"{int(max(stamps)):x}"[-8:]
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
templates.env.globals["document_types"] = DOCUMENT_TYPES
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
