"""ADO-154/ADO-106: единственная точка форматирования времени в web-UI.

Прежний ``nav_service.fmt_relative`` принимал только строку и при `datetime`
молча возвращал вход — в HTML уезжал ``str(datetime)`` с микросекундами.
Здесь закреплены оба входа, обе таймзонные ветки и поведение на мусоре.

Шаблонная сторона (какой фильтр где обязан стоять) — в
tests/api/test_web_template_dates.py; рантайм-инвариант «ни одна страница не
печатает сырой datetime» — в tests/api/test_web_polish.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from cod_doc.api.web.dates import (
    coerce,
    fmt_date,
    fmt_datetime,
    fmt_relative,
    fmt_tooltip,
)

FIXED = datetime(2026, 9, 17, 14, 3, 22, 897000, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# coerce                                                                       #
# --------------------------------------------------------------------------- #


def test_coerce_naive_datetime_is_utc_not_local() -> None:
    """Регресс nav_service.py:130: ``astimezone`` трактовал наивное как локальное."""
    naive = datetime(2026, 9, 17, 14, 3, 22)
    got = coerce(naive)
    assert got is not None
    assert got.tzinfo is UTC
    assert got.hour == 14, "наивное время сдвинулось — значит его снова считают локальным"


def test_coerce_aware_datetime_is_converted_to_utc() -> None:
    aware = datetime(2026, 9, 17, 17, 3, 22, tzinfo=timezone(timedelta(hours=3)))
    got = coerce(aware)
    assert got is not None
    assert got.hour == 14


def test_coerce_naive_iso_string_is_utc() -> None:
    got = coerce("2026-09-17T14:03:22")
    assert got is not None
    assert got.tzinfo is UTC
    assert got.hour == 14


def test_coerce_accepts_trailing_z() -> None:
    got = coerce("2026-09-17T14:03:22Z")
    assert got is not None
    assert got.hour == 14


@pytest.mark.parametrize("value", [None, "", "   ", "не дата", "2026-13-45"])
def test_coerce_returns_none_on_unusable(value: str | None) -> None:
    assert coerce(value) is None


def test_coerce_accepts_bare_date() -> None:
    """Колонка ``sa.Date`` отдаёт `date`, а не `datetime` (``adr.decided_at``).

    Пока ветки не было, `date` проваливалась в строковый разбор и роняла
    страницу списка ADR на ``.strip()``.
    """
    got = coerce(date(2026, 9, 17))
    assert got is not None
    assert got.tzinfo is UTC
    assert (got.year, got.month, got.day) == (2026, 9, 17)
    assert (got.hour, got.minute, got.second) == (0, 0, 0)


def test_coerce_checks_datetime_before_date() -> None:
    """`datetime` — подкласс `date`; обратный порядок срезал бы время."""
    got = coerce(datetime(2026, 9, 17, 14, 3, 22))
    assert got is not None
    assert (got.hour, got.minute) == (14, 3)


def test_date_filter_renders_bare_date() -> None:
    """Регресс 500 на ``/p/<slug>/adr``: ``{{ it.decided_at | short_date }}``."""
    assert fmt_date(date(2026, 9, 17)) == "2026-09-17"


# --------------------------------------------------------------------------- #
# абсолютные форматы                                                           #
# --------------------------------------------------------------------------- #


def test_datetime_has_no_microseconds_and_no_t() -> None:
    out = fmt_datetime(FIXED)
    assert out == "2026-09-17 14:03"
    assert "." not in out
    assert "T" not in out


def test_datetime_accepts_iso_string() -> None:
    assert fmt_datetime("2026-09-17T14:03:22.897000+00:00") == "2026-09-17 14:03"


def test_date_drops_the_time() -> None:
    assert fmt_date(FIXED) == "2026-09-17"


def test_tooltip_keeps_seconds_and_names_the_zone() -> None:
    assert fmt_tooltip(FIXED) == "2026-09-17 14:03:22 UTC"


@pytest.mark.parametrize("fmt", [fmt_datetime, fmt_date, fmt_relative])
def test_none_and_empty_render_em_dash(fmt) -> None:  # type: ignore[no-untyped-def]
    assert fmt(None) == "—"
    assert fmt("") == "—"


def test_tooltip_empty_default_is_blank_not_dash() -> None:
    """В ``title=`` прочерк выглядит как значение; там пусто лучше."""
    assert fmt_tooltip(None) == ""


def test_custom_empty_is_honoured() -> None:
    assert fmt_datetime(None, empty="никогда") == "никогда"


@pytest.mark.parametrize("fmt", [fmt_datetime, fmt_date, fmt_tooltip, fmt_relative])
def test_unparseable_string_falls_back_to_itself(fmt) -> None:  # type: ignore[no-untyped-def]
    """Потерять непонятное значение хуже, чем показать его сырым."""
    assert fmt("что-то своё") == "что-то своё"


# --------------------------------------------------------------------------- #
# относительный формат                                                          #
# --------------------------------------------------------------------------- #


def test_relative_just_now() -> None:
    assert fmt_relative(datetime.now(UTC)) == "just now"


def test_relative_minutes() -> None:
    assert fmt_relative(datetime.now(UTC) - timedelta(minutes=5)) == "5 min ago"


def test_relative_hours_are_pluralised() -> None:
    assert fmt_relative(datetime.now(UTC) - timedelta(hours=1, minutes=1)) == "1 hour ago"
    assert fmt_relative(datetime.now(UTC) - timedelta(hours=2)) == "2 hours ago"


def test_relative_days() -> None:
    assert fmt_relative(datetime.now(UTC) - timedelta(days=3)) == "3 days ago"


def test_relative_handles_future_as_in_prefix() -> None:
    """Единственное, что умел удалённый ``_fmt_age``: routines показывают next_fire_at."""
    assert fmt_relative(datetime.now(UTC) + timedelta(minutes=5)) == "in 5 min"
    assert fmt_relative(datetime.now(UTC) + timedelta(hours=2)) == "in 2 hours"


def test_relative_over_30_days_renders_absolute_date() -> None:
    old = datetime.now(UTC) - timedelta(days=400)
    out = fmt_relative(old)
    assert out == old.strftime("%Y-%m-%d")
    assert "ago" not in out


def test_relative_accepts_iso_string_like_project_stats() -> None:
    """``stats.last_run`` приходит ISO-строкой из сервиса — фильтр обязан её принять."""
    iso = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    assert fmt_relative(iso) == "5 min ago"
