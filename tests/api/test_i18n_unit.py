"""Unit tests for web i18n helpers."""

from __future__ import annotations

from cod_doc.api.web.i18n import set_request_locale, translate


def test_translate_russian_default() -> None:
    set_request_locale("ru")
    assert "Проекты" in str(translate("nav.projects"))


def test_translate_english() -> None:
    set_request_locale("en")
    assert str(translate("nav.projects")) == "Projects"


def test_translate_interpolation_markup() -> None:
    set_request_locale("en")
    out = str(translate("overview.tasks_done", done=3, total=10))
    assert "3" in out and "10" in out
