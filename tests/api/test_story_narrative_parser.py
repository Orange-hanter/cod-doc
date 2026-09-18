"""ADO-144: парсер нарратива понимает и английскую, и русскую формулу.

До этого регекс был один и только английский: на русском корпусе он не
срабатывал ни разу, карточки уходили в сырой абзац, а секция (которая тогда
выводилась из ``id_hint``) не выводилась вообще.
"""

from __future__ import annotations

import time

import pytest

from cod_doc.api.web.pages import stories as stories_page
from cod_doc.api.web.pages.stories import _MAX_NARRATIVE_PARSE, _parse_narrative


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


# ── ADO-144: формы, в которых нарративы написаны НА САМОМ ДЕЛЕ ─────────
#
# Все примеры ниже сняты с живого корпуса cod-doc (31 история). Пока эти
# кейсы отсутствовали, русский паттерн разбирал 0 из 31, а весь файл был
# зелёным: примеры в нём написаны в полной книжной форме
# «Как роль, я хочу X, чтобы Y», которой в БД нет ни разу.


@pytest.mark.parametrize(
    ("text", "want", "so_that"),
    [
        # Самая частая форма: ни «Как …,», ни «я» — роль лежит в persona.
        (
            "хочу автоматически видеть все файлы из context_refs задачи при её "
            "запуске, чтобы не тратить итерации на поиск",
            "автоматически видеть все файлы из context_refs задачи при её запуске",
            "не тратить итерации на поиск",
        ),
        # «хочу чтобы X, чтобы Y» — первое «чтобы» принадлежит телу.
        (
            "хочу чтобы tool palette внутреннего агента совпадал с MCP-палитрой, "
            "чтобы агент мог пользоваться теми же инструментами",
            "чтобы tool palette внутреннего агента совпадал с MCP-палитрой",
            "агент мог пользоваться теми же инструментами",
        ),
        # Запятая сразу после «хочу» (US-029).
        (
            "хочу, чтобы создание задачи проходило через линт-гейт, "
            "чтобы карточка была исполнима с холодного старта",
            "чтобы создание задачи проходило через линт-гейт",
            "карточка была исполнима с холодного старта",
        ),
        # Полная книжная форма обязана продолжать работать.
        (
            "Как управляющий, я хочу видеть отчёт, чтобы принимать решения",
            "видеть отчёт",
            "принимать решения",
        ),
    ],
    ids=["bare-хочу", "хочу-чтобы-чтобы", "хочу-запятая", "полная-форма"],
)
def test_corpus_shaped_narratives_parse(text: str, want: str, so_that: str) -> None:
    p = _parse_narrative(text)
    assert p["want"] == want
    assert p["so_that"] == so_that


def test_narrative_without_a_result_clause_falls_back_to_raw() -> None:
    """«для …» вместо «чтобы» (US-024/US-025) — честный raw, а не угадывание.

    Ветку под «для» заводить нельзя: предлог стоит в середине почти
    каждого предложения, и разбор по нему даст мусор.
    """
    text = (
        "хочу индексировать файловую базу репозитория (RepoIndex) "
        "для более быстрого поиска взаимосвязей"
    )
    p = _parse_narrative(text)
    assert p["want"] == ""
    assert p["so_that"] == ""
    assert p["raw"] == text


# ── ADO-164: стоимость разбора ────────────────────────────────────────


def _one_parse_ms(text: str) -> float:
    t0 = time.perf_counter()
    _parse_narrative(text)
    return (time.perf_counter() - t0) * 1000


def _parse_cost_ms(text: str, *, rounds: int = 5, batch: int = 20) -> float:
    """Минимум по раундам: минимум устойчивее среднего к чужой нагрузке."""
    best = float("inf")
    for _ in range(rounds):
        t0 = time.perf_counter()
        for _ in range(batch):
            _parse_narrative(text)
        best = min(best, (time.perf_counter() - t0) * 1000)
    return best


@pytest.mark.parametrize(
    "prefix",
    ["Как роль, я хочу ", "As a dev, I want "],
    ids=["ru", "en"],
)
def test_whitespace_run_parses_in_linear_time(prefix: str) -> None:
    r"""Пробельный прогон в теле не должен стоить квадрата длины.

    Ловится именно рост, а не абсолютное время: на медленной машине растут
    оба замера.

    Обе длины ОБЯЗАНЫ быть ниже `_MAX_NARRATIVE_PARSE`, иначе большая
    сторона упирается в потолок, возвращается за наносекунды и тест
    перестаёт мерить регекс вообще.
    """
    assert _MAX_NARRATIVE_PARSE > 4_000, "обе длины должны быть под потолком"
    small = prefix + " " * 500 + "x"
    large = prefix + " " * 4_000 + "x"

    # Страховка от зависания стоит первой и меряет ОДИН разбор: до фикса
    # русский паттерн на этом входе считал минутами, и серия замеров ниже
    # просто не закончилась бы.
    guard = _one_parse_ms(small)
    assert guard < 500, f"один разбор 500 символов занял {guard:.1f} мс"

    t_small = _parse_cost_ms(small)
    t_large = _parse_cost_ms(large)
    # Длина выросла в 8×. Линейный разбор даёт примерно столько же,
    # квадратичный — около 64×. Порог 30 отделяет одно от другого и
    # оставляет запас на шум таймера.
    ratio = t_large / max(t_small, 0.05)
    assert t_large < max(t_small, 0.05) * 30, (
        f"рост {ratio:.1f}× при росте длины в 8× — похоже на квадратичный бэктрекинг"
    )


def test_oversized_narrative_falls_back_to_raw() -> None:
    """Выше потолка — тот же честный fallback, что и при несовпадении."""
    text = "Как роль, я хочу X, чтобы Y" + "." * _MAX_NARRATIVE_PARSE
    p = _parse_narrative(text)
    assert p["raw"] == text
    assert p["want"] == ""
    assert p["so_that"] == ""
    assert p["role"] == ""


def test_oversized_narrative_never_reaches_the_regexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Потолок стоит ДО регексов, а не после.

    Проверяется подменой каталога паттернов на часового, который взрывается
    при вызове. Вторая половина теста — его не-вакуумность: на входе под
    потолком часовой обязан сработать, иначе тест ничего не доказывает.
    """

    class _Sentinel:
        def match(self, text: str) -> None:
            raise AssertionError("регекс не должен вызываться выше потолка")

    monkeypatch.setattr(stories_page, "_NARRATIVE_PATTERNS", (_Sentinel(),))

    over = "я хочу X, чтобы Y" + "." * _MAX_NARRATIVE_PARSE
    assert _parse_narrative(over)["raw"] == over

    with pytest.raises(AssertionError, match="не должен вызываться"):
        _parse_narrative("я хочу X, чтобы Y")


@pytest.mark.parametrize(
    "text",
    [
        "Как роль, я хочу X, чтобы Y",
        "Как роль, я хочу X , чтобы Y",
        "Как роль, я хочу X,чтобы Y",
        "Как роль, я хочу X чтобы Y",
        "Как роль, я хочу   X,   чтобы Y",
    ],
)
def test_russian_bridge_accepts_all_spacings(text: str) -> None:
    r"""Мост `[\s,]*` заменил `\s*,?\s*` — разбор обязан остаться тем же."""
    p = _parse_narrative(text)
    assert p["role"] == "роль"
    assert p["want"] == "X"
    assert p["so_that"] == "Y"
