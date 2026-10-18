"""Слияние статуса задачи по цепочке записей (RFC 29 §3.7, Q-011; CLO-020, CLO-044).

Поля стратегии ``status`` — ``task.status`` и поля замка checkout
(``checked_out_by``, ``checked_out_at``). Их значение — не победитель пары
«наше против чужого», а **проигрывание цепочки** всех записей поля, которые
знает реплика (своя история, свой хвост, чужие применённые операции и
операции текущего changeset'а), по штампу ``(hlc, op_id)`` от общего начала:

1. Начало — ``base`` самой ранней записи (у операции создания — ``None``).
2. Запись, чей ``base`` равен текущему значению проигрывания, —
   **последовательная**: её автор видел это состояние, и она применяется
   как есть. Переход проверил (или нет) пишущий сервис; линейная история
   одной реплики поэтому не судится никогда.
3. Запись, чей ``base`` с текущим значением расходится, —
   **конкурентная**: её автор писал поверх состояния, которого в цепочке уже
   нет. Она применяется, только если её допускает правило колонки
   (``_admits``); иначе значение остаётся прежним, а запись становится
   отказом — конфликтом ``status`` или ``double_checkout``.

Правило статуса — ``task_status_machine.ALLOWED_TRANSITIONS`` из текущего
значения в значение записи (легаси-написания сводятся ``normalise``; одно
значение в разных написаниях — без проверки). Правило замка — двойной захват:
запись взяла свободный замок (``base`` пуст, значение есть), а в цепочке его
уже держит другое значение. Всё остальное на замке (снятие против захвата,
перехват) применяется в порядке штампов — это LWW.

**Почему цепочка, а не пара** (F11 аудита секции C). Попарная проверка у
трёх записей зависела от порядка доставки: промежуточная запись проигрывала
допустимым переходом, обрезалась из хвоста и не уезжала, а третья реплика
судила пару, которой в истории не было. Проигрывание зависит только от
множества записей, а записи этих полей из хвоста не обрезаются никогда
(``merge.fields_to_apply``): проигравшая уезжает, и каждая реплика рано или
поздно держит всю цепочку. Итог — одно значение и одно множество отказов на
всех репликах при любом числе записей и любом порядке обмена.

**Отличие от «в БД остаётся наше».** У конфликта цепочки ``ours`` —
значение, на котором стоит проигрывание (оно в БД на каждой реплике),
``theirs`` — отвергнутая запись, ``base`` — её ``base``. Своя запись тоже
может оказаться отвергнутой, когда до реплики доезжает более ранняя чужая.

``resolve`` — тот же расчёт для пары записей из одного общего начала; ядро
слияния его не зовёт, он нужен ``merge.check`` (стратегия вида есть) и как
сжатая форма правила.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from cod_doc.services.sync_service.changeset import canonical_json
from cod_doc.services.sync_service.merge import Outcome, Resolution
from cod_doc.services.task_status_machine import ALLOWED_TRANSITIONS, normalise

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cod_doc.services.sync_service.changeset import FieldValue
    from cod_doc.services.sync_service.merge import FieldClash

__all__ = [
    "CHAIN_COLUMNS",
    "CHECKOUT_COLUMNS",
    "DOUBLE_CHECKOUT",
    "STATUS",
    "Rejection",
    "Replay",
    "Write",
    "conflict_kind",
    "replay",
    "resolve",
]

STATUS: Final = "status"
DOUBLE_CHECKOUT: Final = "double_checkout"
CHECKOUT_COLUMNS: Final = frozenset({"checked_out_by", "checked_out_at"})
#: Колонки задачи, которые решает цепочка, в порядке проигрывания.
CHAIN_COLUMNS: Final = ("status", "checked_out_by", "checked_out_at")

#: Штамп записи: ``(hlc, op_id)``.
type Stamp = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Write:
    """Одна запись поля: штамп операции, новое значение и ``base`` (форма §10.4)."""

    hlc: str
    op_id: str
    value: FieldValue
    base: FieldValue

    @property
    def stamp(self) -> Stamp:
        return (self.hlc, self.op_id)


@dataclass(frozen=True, slots=True)
class Rejection:
    """Отвергнутая конкурентная запись и значение, на котором стояло проигрывание."""

    write: Write
    current: FieldValue


@dataclass(frozen=True, slots=True)
class Replay:
    """Итог проигрывания цепочки одной колонки."""

    value: FieldValue
    rejected: tuple[Rejection, ...]


def conflict_kind(column: str) -> str:
    """Вид конфликта отказа в колонке: ``double_checkout`` у замка, иначе ``status``."""
    return DOUBLE_CHECKOUT if column in CHECKOUT_COLUMNS else STATUS


def _canonical(value: object) -> str | None:
    """Канонический бакет; незнакомое значение остаётся как есть (переходов у него нет)."""
    return normalise(value) if isinstance(value, str) else None


def _same(column: str, a: FieldValue, b: FieldValue) -> bool:
    if column == STATUS and isinstance(a, str) and isinstance(b, str):
        return _canonical(a) == _canonical(b)
    return canonical_json(a) == canonical_json(b)


def _admits(column: str, current: FieldValue, write: Write) -> bool:
    """Правило колонки для конкурентной записи."""
    if _same(column, current, write.value):
        return True
    if column in CHECKOUT_COLUMNS:
        took_free = write.base is None and write.value is not None
        return not (took_free and current is not None)
    frm, to = _canonical(current), _canonical(write.value)
    if frm is None or to is None:
        return False  # не строка: переход не проверить
    return to in ALLOWED_TRANSITIONS.get(frm, frozenset())


def replay(column: str, writes: Iterable[Write]) -> Replay:
    """Проиграть записи колонки по ``(hlc, op_id)``; результат не зависит от порядка входа.

    Повтор одной операции (один ``op_id``) считается один раз.
    """
    chain = sorted({w.op_id: w for w in writes}.values(), key=lambda w: w.stamp)
    if not chain:
        return Replay(value=None, rejected=())
    current = chain[0].base
    rejected: list[Rejection] = []
    for write in chain:
        if _same(column, write.base, current) or _admits(column, current, write):
            current = write.value
        else:
            rejected.append(Rejection(write=write, current=current))
    return Replay(value=current, rejected=tuple(rejected))


def resolve(clash: FieldClash) -> Resolution:
    """Пара записей из общего начала (``ours_base``) — тем же проигрыванием (§3.7)."""
    ours = Write(clash.ours_stamp[0], clash.ours_stamp[1], clash.ours, clash.ours_base)
    theirs = Write(clash.theirs_stamp[0], clash.theirs_stamp[1], clash.theirs, clash.theirs_base)
    if replay(clash.column, [ours, theirs]).rejected:
        return Resolution(Outcome.CONFLICT, conflict_kind=conflict_kind(clash.column))
    return Resolution(Outcome.THEIRS if clash.theirs_stamp > clash.ours_stamp else Outcome.OURS)
