"""ADO-144: парсер нарратива понимает и английскую, и русскую формулу.

До этого регекс был один и только английский: на русском корпусе он не
срабатывал ни разу, карточки уходили в сырой абзац, а секция (которая тогда
выводилась из ``id_hint``) не выводилась вообще.
"""

from __future__ import annotations

import pytest

from cod_doc.api.web.pages.stories import _parse_narrative


def test_english_narrative_still_parses() -> None:
    p = _parse_narrative("As a developer, I want quick search, so that I find things fast")
    assert p["role"] == "developer"
    assert p["want"] == "quick search"
    assert p["so_that"] == "I find things fast"


def test_english_header_yields_id_hint_and_title() -> None:
    p = _parse_narrative("[US-1.1 Quick search] As a developer, I want X, so that Y")
    assert p["id_hint"] == "US-1.1"
    assert p["title"] == "Quick search"


def test_russian_narrative_parses() -> None:
    p = _parse_narrative(
        "Как управляющий, я хочу получать ежедневные уведомления о позициях, "
        "чтобы избегать стоп-листов"
    )
    assert p["role"] == "управляющий"
    assert p["want"].startswith("получать ежедневные уведомления")
    assert p["so_that"] == "избегать стоп-листов"


def test_russian_without_comma_before_chtoby() -> None:
    p = _parse_narrative("Как закупщик, я хочу видеть цены чтобы экономить")
    assert p["role"] == "закупщик"
    assert p["want"] == "видеть цены"
    assert p["so_that"] == "экономить"


def test_russian_header_yields_id_hint() -> None:
    p = _parse_narrative("[US-2.1 Заявки] Как закупщик, я хочу X, чтобы Y")
    assert p["id_hint"] == "US-2.1"
    assert p["title"] == "Заявки"
    assert p["role"] == "закупщик"


@pytest.mark.parametrize(
    "text",
    [
        "Просто абзац без всякой формулы",
        # Annex-форма «Как <роль>: <тема>» — это не user story, а строка таблицы.
        "Как Гость / персонал: QR-меню: витрина S2 + заказы S3",
        "",
    ],
)
def test_unparseable_falls_back_to_raw(text: str) -> None:
    """Честный fallback: не угадываем, а показываем сырой текст."""
    p = _parse_narrative(text)
    assert p["want"] == ""
    assert p["so_that"] == ""
    assert p["raw"] == text


def test_both_languages_return_the_same_field_set() -> None:
    en = _parse_narrative("As a dev, I want X, so that Y")
    ru = _parse_narrative("Как разработчик, я хочу X, чтобы Y")
    assert en.keys() == ru.keys()
    assert all(en[k] and ru[k] for k in ("role", "want", "so_that"))
