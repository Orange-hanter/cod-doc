"""Форматирование времени для web-UI — единственная точка на весь фронтенд.

ADO-154. До этого модуля фильтр был один — ``relative_time``
(``nav_service.fmt_relative``), и он принимал **строку**: при `datetime` ветка
``except (ValueError, TypeError)`` молча возвращала вход, и в HTML уезжал
``str(datetime)`` вида ``2026-09-08 03:21:28.897000``. Абсолютного форматтера
не было вовсе, поэтому 19 мест в шаблонах печатали дату кто как: сырым
значением, ``strftime``, ``replace("T", " ")``, срезами ``[:10]``/``[:19]`` и
вторым серверным хелпером ``_fmt_age`` в ``pages/routines.py``.

Почему здесь, а не в ``services/``: рендер времени для браузера — презентация.
``fmt_relative`` жил в ``nav_service`` исторически (рядом с
``NavAnalysis.analyzed_at``) и не имел ни одного потребителя вне web-слоя.

Правило для хендлеров (капабилити web-frontend §5): страница отдаёт доменное
значение как есть. ``.isoformat()`` в ``cod_doc/api/web/pages/`` законен только
при сериализации — ``JSONResponse`` или JSON-файл на диске, — но никогда для
контекста шаблона. Поэтому фильтры принимают и ``datetime``, и ``str``.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime

DateLike = datetime | date | str | None

_MINUTE = 60
_HOUR = 3600
_DAY = 86400
_RELATIVE_HORIZON_DAYS = 30

#: Абсолютный формат по умолчанию: без `T`, без микросекунд, без таймзоны в ячейке.
_SHORT_DATETIME = "%Y-%m-%d %H:%M"
_SHORT_DATE = "%Y-%m-%d"
_TOOLTIP = "%Y-%m-%d %H:%M:%S UTC"


def coerce(value: DateLike) -> datetime | None:
    """Привести значение к aware-datetime в UTC. ``None`` — если не вышло.

    Наивный `datetime` считается уже UTC (``replace``, не ``astimezone``):
    БД хранит время в UTC, и прежний ``astimezone`` трактовал наивную строку
    как локальную, сдвигая дельту на offset машины.

    Голая `date` (колонка ``sa.Date`` — сейчас это только ``adr.decided_at``)
    разворачивается в полночь UTC. Порядок проверок значим: `datetime` —
    подкласс `date`, и обратная последовательность срезала бы время у всех
    штампов. Пока ветки не было, ``/p/<slug>/adr`` падал с 500 на
    ``'datetime.date' object has no attribute 'strip'`` — список ADR читает
    `decided_at` доменной сущностью как есть, тогда как detail-страница
    проходила через ``adr_to_dict`` и отдавала строку.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith(("Z", "z")):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _fallback(value: DateLike, empty: str) -> str:
    """Что показать, когда значение не разобралось.

    Пустое — ``empty``. Непустая нераспознанная строка возвращается как есть:
    прежний ``fmt_relative`` вёл себя так же, и терять непонятное значение
    хуже, чем показать его сырым.
    """
    if value is None:
        return empty
    if isinstance(value, str) and value.strip():
        return value
    return empty


def fmt_datetime(value: DateLike, empty: str = "—") -> str:
    """``2026-09-17 14:03`` — абсолютное время для колонок таблиц."""
    ts = coerce(value)
    return ts.strftime(_SHORT_DATETIME) if ts else _fallback(value, empty)


def fmt_date(value: DateLike, empty: str = "—") -> str:
    """``2026-09-17`` — там, где время не несёт смысла (дата решения, коммита)."""
    ts = coerce(value)
    return ts.strftime(_SHORT_DATE) if ts else _fallback(value, empty)


def fmt_tooltip(value: DateLike, empty: str = "") -> str:
    """``2026-09-17 14:03:22 UTC`` — полное время в ``title=``, не в ячейке."""
    ts = coerce(value)
    return ts.strftime(_TOOLTIP) if ts else _fallback(value, empty)


def fmt_relative(value: DateLike, empty: str = "—") -> str:
    """``just now`` / ``5 min ago`` / ``in 2 hours`` / ``2026-09-17``.

    Будущее поддержано (``in …``): это единственное, что умел удалённый
    ``_fmt_age`` из ``pages/routines.py`` и не умел прежний ``fmt_relative``,
    а таблице routines нужен ``next_fire_at``.

    Дальше месяца относительная форма перестаёт что-либо сообщать — там
    возвращается абсолютная дата.
    """
    ts = coerce(value)
    if ts is None:
        return _fallback(value, empty)

    delta = (datetime.now(UTC) - ts).total_seconds()
    future = delta < 0
    delta = abs(delta)

    if delta < _MINUTE:
        return "just now"
    if delta >= _DAY * _RELATIVE_HORIZON_DAYS:
        return ts.strftime(_SHORT_DATE)

    # Прошлое округляем вниз (4:59 назад — «4 min ago»), будущее вверх:
    # «in 4 min» за 4:59 до срабатывания читается как обещание, которое
    # нарушат. Тот же выбор делал удалённый `_fmt_age`.
    round_ = math.ceil if future else math.floor

    if delta < _HOUR:
        amount, unit = int(round_(delta / _MINUTE)), "min"
    elif delta < _DAY:
        amount, unit = int(round_(delta / _HOUR)), "hour"
    else:
        amount, unit = int(round_(delta / _DAY)), "day"

    plural = "s" if unit != "min" and amount > 1 else ""
    phrase = f"{amount} {unit}{plural}"
    return f"in {phrase}" if future else f"{phrase} ago"
