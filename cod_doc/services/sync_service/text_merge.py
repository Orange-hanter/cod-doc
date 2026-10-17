"""3-way merge текстовых колонок построчно от ``base`` (RFC 29 §3.7; CLO-019).

Колонки — ``text_columns`` реестра: ``section.body``, ``task.description``,
``task.acceptance``, ``task_document.body``, ``adr.decision``,
``user_story.narrative``. Диспетчер ``merge`` зовёт ``resolve``, когда хвост
реплики и чужая операция записали одно такое поле разными значениями.

**Алгоритм — diff3.** Каждая сторона сравнивается с ``base`` отдельно
(``difflib.SequenceMatcher`` без autojunk), правка стороны — набор ханков
``[start, end)`` по строкам ``base`` с заменой. Ханки обеих сторон
сортируются по ``(start, end)`` и склеиваются в кластеры по пересечению
диапазонов; внутри стороны ханки не пересекаются по построению. Кластер с
ханками одной стороны берёт её версию, кластер обеих сторон — равные версии
поглощаются, разные дают конфликт ``text``.

Пересечение — общая строка ``base``; две вставки в одну точку тоже
пересекаются (порядок между ними не выводится), вставка строго внутри чужого
диапазона — тоже. Вставка на границе чужого диапазона и соседние диапазоны
не пересекаются: вставка в точке ``p`` встаёт перед диапазоном, который в
``p`` начинается, и после того, который в ``p`` кончается.

**Симметрия.** Кластеризация и выбор версии смотрят только на диапазоны
``base`` и текст замены, а не на то, какая сторона «наша», поэтому
``merge3(base, a, b) == merge3(base, b, a)`` для любой пары без конфликта
(проверено тестом). Отсюда детерминизм между репликами: какая бы реплика ни
сливала, сведённый текст один и тот же.

**Исходы для диспетчера.** Сведённое совпало с «их» — ``theirs`` (своё поле
уходит из хвоста). Иначе ``merged`` — даже когда сведённое совпало с «нашим»:
наш штамп мог проиграть чужому на реплике, которая чужое уже приняла, а
``merged`` уезжает со свежим штампом и перекрывает его везде. Конфликт —
``conflict`` с видом ``text``: в БД остаётся «наше», «их» лежит в
``sync_conflict``.

**База.** ``base`` у своей и чужой операции совпадают, когда обе правили одно
состояние поля, — это обычный случай (ядро держит базу сведённой операции
равной чужому значению). Если базы разные, общего предка нет: такие тексты
сливать построчно нельзя, это конфликт. ``NULL`` в базе — пустой текст:
два тела, написанные с нуля, сходятся, только если равны (так же RFC §3.3
решает тела склеенных сущностей без общего предка).

**Строки.** Делим только по ``\\n``, конец строки остаётся в строке: ``\\r\\n``
сохраняется как есть, последняя строка без перевода — отдельное значение
(дописать строку после неё — значит изменить и её).
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from cod_doc.services.sync_service.merge import Outcome, Resolution

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cod_doc.services.sync_service.changeset import FieldValue
    from cod_doc.services.sync_service.merge import FieldClash

__all__ = ["Conflict", "merge3", "resolve", "split_lines"]

TEXT: Final = "text"
_EQUAL: Final = "equal"


class Conflict(Exception):
    """Пересекающиеся ханки с разным текстом."""


@dataclass(frozen=True, slots=True)
class _Hunk:
    start: int
    end: int
    lines: tuple[str, ...]
    side: int


def split_lines(text: str) -> list[str]:
    """Строки с концами; делим только по ``\\n`` (``\\r\\n`` остаётся в строке)."""
    if not text:
        return []
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _hunks(base: Sequence[str], side: Sequence[str], tag: int) -> list[_Hunk]:
    matcher = difflib.SequenceMatcher(None, base, side, autojunk=False)
    return [
        _Hunk(i1, i2, tuple(side[j1:j2]), tag)
        for op, i1, i2, j1, j2 in matcher.get_opcodes()
        if op != _EQUAL
    ]


def _overlaps(cluster_start: int, cluster_end: int, hunk: _Hunk) -> bool:
    """Пересекается ли ханк с кластером ``[cluster_start, cluster_end)``."""
    if hunk.start < cluster_end:
        return True
    # Обе — вставки в одну точку: порядок между ними не выводится.
    return hunk.start == hunk.end == cluster_start == cluster_end


def _clusters(hunks: list[_Hunk]) -> list[list[_Hunk]]:
    out: list[list[_Hunk]] = []
    cluster_end = -1
    cluster_start = -1
    for hunk in sorted(hunks, key=lambda h: (h.start, h.end, h.side)):
        if out and _overlaps(cluster_start, cluster_end, hunk):
            out[-1].append(hunk)
            cluster_end = max(cluster_end, hunk.end)
            continue
        out.append([hunk])
        cluster_start, cluster_end = hunk.start, hunk.end
    return out


def _version(base: Sequence[str], start: int, end: int, hunks: Sequence[_Hunk]) -> list[str]:
    """Текст стороны на ``base[start:end]``: её ханки поверх базы."""
    out: list[str] = []
    pos = start
    for hunk in hunks:
        out.extend(base[pos : hunk.start])
        out.extend(hunk.lines)
        pos = hunk.end
    out.extend(base[pos:end])
    return out


def merge3(base: str, ours: str, theirs: str) -> str:
    """Свести две правки ``base``; пересекающиеся разные ханки — ``Conflict``."""
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    base_lines = split_lines(base)
    hunks = _hunks(base_lines, split_lines(ours), 0) + _hunks(base_lines, split_lines(theirs), 1)
    out: list[str] = []
    pos = 0
    for cluster in _clusters(hunks):
        start = min(h.start for h in cluster)
        end = max(h.end for h in cluster)
        out.extend(base_lines[pos:start])
        sides = [[h for h in cluster if h.side == tag] for tag in (0, 1)]
        versions = [_version(base_lines, start, end, side) for side in sides if side]
        if any(v != versions[0] for v in versions[1:]):
            raise Conflict(f"строки {start + 1}–{max(end, start + 1)} правлены обеими сторонами")
        out.extend(versions[0])
        pos = end
    out.extend(base_lines[pos:])
    return "".join(out)


def _text(value: FieldValue) -> str | None:
    if value is None:
        return ""
    return value if isinstance(value, str) else None


def resolve(clash: FieldClash) -> Resolution:
    """Стратегия ``text`` для диспетчера ``merge`` (§3.7)."""
    conflict = Resolution(Outcome.CONFLICT, conflict_kind=TEXT)
    base, ours, theirs = (
        _text(clash.theirs_base),
        _text(clash.ours),
        _text(clash.theirs),
    )
    if base is None or ours is None or theirs is None:
        return conflict
    if _text(clash.ours_base) != base:
        return conflict  # разные базы: общего предка нет
    try:
        merged = merge3(base, ours, theirs)
    except Conflict:
        return conflict
    if merged == theirs:
        return Resolution(Outcome.THEIRS)
    if not merged and clash.ours is None:
        return Resolution(Outcome.MERGED, value=None)  # пусто и было NULL — остаётся NULL
    return Resolution(Outcome.MERGED, value=merged)
