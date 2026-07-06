"""Locale message catalogs for the web UI."""

from __future__ import annotations

from .en import MESSAGES as EN
from .ru import MESSAGES as RU

CATALOGS: dict[str, dict[str, str]] = {
    "en": EN,
    "ru": RU,
}

SUPPORTED_LOCALES: tuple[str, ...] = ("ru", "en")
DEFAULT_LOCALE = "ru"
COOKIE_NAME = "cod-doc-locale"
