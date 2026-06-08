"""Jinja2 environment for the web frontend."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote

from fastapi.templating import Jinja2Templates

from cod_doc.domain.entities import DocumentType, TaskStatus
from cod_doc.services.nav_service import fmt_relative as _fmt_relative

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates" / "web"
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"

TASK_STATUS_OPTIONS: list[str] = [s.value for s in TaskStatus]
DOCUMENT_TYPES: list[str] = [t.value for t in DocumentType]


# WEB-051: static asset cache-bust.
# Compute a short fingerprint from each file's mtime once at import; templates
# call `static_url("app.css")` to get `/static/app.css?v=<hash>`. Browsers cache
# until the file changes; on bump → new query string → fresh fetch.
_STATIC_VERSION_CACHE: dict[str, str] = {}


def _fingerprint(name: str) -> str:
    """Hex-formatted mtime of the static file. Empty string if missing."""
    if name not in _STATIC_VERSION_CACHE:
        path = STATIC_DIR / name
        try:
            stat = path.stat()
        except OSError:
            _STATIC_VERSION_CACHE[name] = ""
        else:
            # 8 hex chars of mtime are enough to bust browser cache.
            _STATIC_VERSION_CACHE[name] = f"{int(stat.st_mtime):x}"[-8:]
    return _STATIC_VERSION_CACHE[name]


def static_url(name: str) -> str:
    """Return /static/<name>?v=<fingerprint> for cache-bust on file change."""
    fp = _fingerprint(name)
    return f"/static/{name}?v={fp}" if fp else f"/static/{name}"


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# `urldecode` lets base.html surface percent-encoded flash cookies (which we
# encode at write time because cookie headers are latin-1).
templates.env.filters["urldecode"] = unquote

templates.env.filters["relative_time"] = _fmt_relative
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
