"""Lightweight i18n for Jinja2 templates — cookie + Accept-Language."""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from typing import TYPE_CHECKING

from markupsafe import Markup, escape

from cod_doc.api.web.locales import CATALOGS, COOKIE_NAME, DEFAULT_LOCALE, SUPPORTED_LOCALES

if TYPE_CHECKING:
    from starlette.requests import Request

_current_locale: ContextVar[str] = ContextVar("locale", default=DEFAULT_LOCALE)

# Keys exported to client-side JS (search palette, WS labels).
JS_KEYS: tuple[str, ...] = (
    "search.empty_prompt",
    "search.no_results",
    "search.hint",
    "ws.state.idle",
    "ws.state.connecting",
    "ws.state.connected",
    "ws.state.reconnecting",
    "ws.live_updates",
    "index.creating",
    "index.created",
    "index.required_fields",
    "index.error_prefix",
    "overview.no_ready_tasks",
)


def resolve_locale(request: Request | None) -> str:
    """Pick locale: cookie → query ?lang= → Accept-Language → env default."""
    if request is None:
        return os.environ.get("COD_DOC_LOCALE", DEFAULT_LOCALE)

    cookie = request.cookies.get(COOKIE_NAME)
    if cookie in SUPPORTED_LOCALES:
        return cookie

    q = request.query_params.get("lang")
    if q in SUPPORTED_LOCALES:
        return q

    accept = (request.headers.get("accept-language") or "").lower()
    if accept.startswith("en") or ",en" in accept:
        return "en"
    if accept.startswith("ru") or ",ru" in accept:
        return "ru"

    return os.environ.get("COD_DOC_LOCALE", DEFAULT_LOCALE)


def set_request_locale(locale: str) -> None:
    if locale in SUPPORTED_LOCALES:
        _current_locale.set(locale)


def get_locale() -> str:
    return _current_locale.get()


def translate(key: str, /, **kwargs: object) -> str | Markup:
    """Return translated string; missing keys fall back to English then key."""
    locale = get_locale()
    catalog = CATALOGS.get(locale) or CATALOGS["ru"]
    text = catalog.get(key) or CATALOGS["en"].get(key) or key
    if kwargs:
        safe_kwargs = {k: escape(v) for k, v in kwargs.items()}
        return Markup(text.format(**safe_kwargs))
    return text


def js_messages() -> str:
    """JSON blob of strings for cod_doc_search.js / cod_doc_ws.js."""
    locale = get_locale()
    catalog = CATALOGS.get(locale) or CATALOGS["ru"]
    payload = {k: catalog.get(k) or CATALOGS["en"].get(k, k) for k in JS_KEYS}
    return json.dumps(payload, ensure_ascii=False)
