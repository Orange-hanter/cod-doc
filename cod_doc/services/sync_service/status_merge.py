"""Слияние статуса задачи через статус-машину (RFC 29 §3.7, Q-011; CLO-020).

Диспетчер ``merge`` зовёт ``resolve``, когда хвост реплики и чужая операция
записали разные значения в ``task.status`` или в поля замка checkout
(``checked_out_by``, ``checked_out_at``).

**Статус.** Побеждает больший штамп ``(hlc, op_id)`` — но только если в
значение победителя можно перейти из значения проигравшего по
``task_status_machine.ALLOWED_TRANSITIONS`` (легаси-написания сводятся
``normalise``). Недопустимая пара — конфликт ``status``: в БД остаётся
«наше», «их» лежит в ``sync_conflict``. Правило смотрит только на пару
значений и штампы, а не на то, какая реплика сливает, поэтому обе реплики
при любом порядке синхронизации решают одинаково: или обе приходят к
победителю, или у сливающей открывается один и тот же конфликт.

Победил свой — ``ours``: свой штамп больше чужого, и реплика, принявшая
чужое, примет и своё по истории (B14). Одно значение в разных написаниях
(``pending``/``todo``) — обычный LWW.

**Двойной офлайн-checkout (Q-011).** С remote checkout берёт замок CAS'ом,
офлайн (``--offline``) — провизорный, и двойной захват ловится при pull.
Признак — обе стороны взяли свободный замок: базы ``checked_out_*`` у обеих
пустые, значения у обеих есть. ``status`` при этом одинаковый
(``in_progress``) и поглощается ядром, а ``checked_out_at`` различается
почти всегда — поэтому граница стоит на полях замка, а не на статусе. Исход —
конфликт ``double_checkout`` про задачу (без поля, один на задачу), в БД
остаётся свой замок; разрешение — механика аренды CLO-031. Всё остальное на
этих полях (снятие замка против захвата, перехват) — LWW.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from cod_doc.services.sync_service.merge import Outcome, Resolution, lww
from cod_doc.services.task_status_machine import ALLOWED_TRANSITIONS, normalise

if TYPE_CHECKING:
    from cod_doc.services.sync_service.merge import FieldClash

__all__ = ["CHECKOUT_COLUMNS", "DOUBLE_CHECKOUT", "STATUS", "resolve"]

STATUS: Final = "status"
DOUBLE_CHECKOUT: Final = "double_checkout"
CHECKOUT_COLUMNS: Final = frozenset({"checked_out_by", "checked_out_at"})


def _checkout(clash: FieldClash) -> Resolution:
    took_free = clash.ours_base is None and clash.theirs_base is None
    if took_free and clash.ours is not None and clash.theirs is not None:
        return Resolution(Outcome.CONFLICT, conflict_kind=DOUBLE_CHECKOUT)
    return lww(clash)


def _canonical(value: object) -> str | None:
    """Канонический бакет; незнакомое значение остаётся как есть (переходов у него нет)."""
    return normalise(value) if isinstance(value, str) else None


def _status(clash: FieldClash) -> Resolution:
    conflict = Resolution(Outcome.CONFLICT, conflict_kind=STATUS)
    ours, theirs = _canonical(clash.ours), _canonical(clash.theirs)
    if ours is None or theirs is None:
        return conflict  # не строка: переход не проверить
    if ours == theirs:
        return lww(clash)
    theirs_wins = clash.theirs_stamp > clash.ours_stamp
    loser, winner = (ours, theirs) if theirs_wins else (theirs, ours)
    if winner not in ALLOWED_TRANSITIONS.get(loser, frozenset()):
        return conflict
    return Resolution(Outcome.THEIRS if theirs_wins else Outcome.OURS)


def resolve(clash: FieldClash) -> Resolution:
    """Стратегия ``status`` для диспетчера ``merge`` (§3.7)."""
    if clash.column in CHECKOUT_COLUMNS:
        return _checkout(clash)
    return _status(clash)
