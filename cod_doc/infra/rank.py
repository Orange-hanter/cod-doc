"""Дробный ранг — синхронизируемый порядок строк (RFC 29 §3.2, Q-009, CLO-003).

Ранг — строка из цифр base-36 (``0-9a-z``), сравнивается побайтно как обычная
строка: ``ORDER BY rank`` в SQLite (``BINARY``) и ``sorted()`` в Python дают
один порядок. Алфавит без заглавных букв нарочно: у PostgreSQL с локалью
``en_US`` регистр в сортировке не значим, и ``"B" < "a"`` там было бы ложью.

Вставка между соседями пишет один новый ранг и не трогает остальных — две
реплики, вставившие строки в разные места одного списка, сливаются без
конфликта. Две реплики, вставившие в одно и то же место, получают равные ранги;
их порядок решает ``uid`` (``order_key``), а не этот модуль.

Сгенерированный ранг никогда не кончается на ``0``: между ``"a"`` и ``"a0"`` нет
ни одной строки, и такая пара исчерпала бы место для вставки. Чужой ранг с
хвостовым ``0`` (импорт, ручная правка) принимается, пока между соседями есть
место.

Модуль — только stdlib. Миграция 0050 его не импортирует (миграции заморожены
и несут свою копию ``initial``), им пользуется ORM-хелпер ``infra/ordering.py``.
"""

from __future__ import annotations

DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
BASE = len(DIGITS)
_INDEX = {ch: i for i, ch in enumerate(DIGITS)}
_LAST = DIGITS[-1]
#: Минимальная ширина ``initial``: запас для вставок перед первым и после последнего.
_MIN_WIDTH = 2


def _check(rank: str) -> None:
    if not rank or any(ch not in _INDEX for ch in rank):
        raise ValueError(f"rank must be a non-empty base-36 string, got {rank!r}")


def _midpoint(a: str, b: str | None) -> str:
    """Строка строго между ``a`` и ``b``; ``a`` может быть пустой, ``b`` — ``None`` (∞)."""
    if b is not None:
        n = 0
        while n < len(b) and (a[n] if n < len(a) else "0") == b[n]:
            n += 1
        if n:
            return b[:n] + _midpoint(a[n:], b[n:])
    digit_a = _INDEX[a[0]] if a else 0
    digit_b = _INDEX[b[0]] if b else BASE
    if digit_b - digit_a > 1:
        return DIGITS[(digit_a + digit_b + 1) // 2]
    if b is not None and len(b) > 1:
        return b[0]
    return DIGITS[digit_a] + _midpoint(a[1:], None)


def _after(a: str) -> str:
    """Ранг после ``a``: увеличить первую цифру, которая ещё не ``z``.

    Дописывание в конец — самая частая вставка (шаг сценария, критерий
    истории). Середина между ``a`` и бесконечностью удлиняла бы ранг на цифру
    каждые ~5 вставок, инкремент — раз в ~36.
    """
    for i, ch in enumerate(a):
        if ch != _LAST:
            return a[:i] + DIGITS[_INDEX[ch] + 1]
    return a + DIGITS[1]


def between(a: str | None, b: str | None) -> str:
    """Ранг строго между ``a`` и ``b``; ``None`` — край списка.

    ``between(None, None)`` — ранг единственной строки. ``a >= b`` и пара без
    места между (``"a"``, ``"a0"``) — ``ValueError``: молча вернуть ранг вне
    интервала значило бы перемешать список.
    """
    if a is not None:
        _check(a)
    if b is not None:
        _check(b)
        if b.rstrip("0") == (a or "").rstrip("0") or (a is not None and a >= b):
            raise ValueError(f"no rank between {a!r} and {b!r}")
    if b is None:
        return _after(a) if a is not None else DIGITS[BASE // 2]
    result = _midpoint(a or "", b)
    if not ((a is None or a < result) and result < b):  # инвариант, не ввод
        raise AssertionError(f"rank {result!r} outside ({a!r}, {b!r})")
    return result


def initial(n: int) -> list[str]:
    """``n`` возрастающих рангов, равномерно в пространстве фиксированной ширины.

    Ширина подобрана так, чтобы между соседями, до первого и после последнего
    оставалось место; хвостовые ``0`` срезаны (порядок от этого не меняется:
    у строк равной ширины срез хвоста сохраняет сравнение).
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    width = _MIN_WIDTH
    while BASE**width < (n + 1) * BASE:
        width += 1
    step = BASE**width // (n + 1)
    return [_encode(step * (i + 1), width).rstrip("0") for i in range(n)]


def _encode(value: int, width: int) -> str:
    digits = []
    for _ in range(width):
        value, rem = divmod(value, BASE)
        digits.append(DIGITS[rem])
    return "".join(reversed(digits))
