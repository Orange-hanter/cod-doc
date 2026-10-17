"""Слияние чужих операций с локальной историей: диспетчер стратегий (RFC 29 §3.7; CLO-018).

**Где возникает расхождение.** Changeset появляется только после удачного CAS
(``push``), поэтому локальная голова — всегда предок головы remote, и
расходиться может лишь неупакованный хвост ``sync_op`` с чужими операциями.
Merge-changeset с двумя родителями (§3.6) строить не из чего: хвост ещё не
changeset. Операции, разрешившие расхождение, ложатся в тот же хвост — так
хвост встаёт поверх новой головы (решение B12 секции B) и уезжает следующим
push'ем. ``PullResult.fast_forward`` — ``False``, если у реплики был свой
хвост (§3.6: «если локального хвоста нет — fast-forward»).

**Инвариант — детерминизм.** Две реплики, применившие одно множество
changeset'ов в любом порядке, приходят к одному дайджесту. Поэтому правило
одно и для хвоста, и для истории: у каждого поля побеждает запись с большим
штампом ``(hlc, op_id)``. HLC — строка ``<ms>.<counter>.<replica_uid>`` с
фиксированной шириной, и её лексикографический порядок — порядок ``(ms,
counter, replica_uid)``: ничья по времени решается uid реплики одинаково на
всех репликах; ``op_id`` (ULID) разводит два процесса одной реплики с
одинаковым HLC (D14).

**Что делает применение** (``fields_to_apply``) для каждого поля чужой
операции над существующей строкой:

1. Хвост пишет то же поле. Равные значения поглощаются: поле уходит из
   хвоста, операции нет (§3.7, Q-013). Иначе — стратегия по виду поля
   (``classify``): победил чужой — поле применяется и уходит из хвоста;
   победил свой — поле не применяется, хвост его увезёт; ``merged`` — в БД и
   в хвост новой операцией со свежим штампом идёт сведённое значение;
   ``conflict`` — в БД остаётся «наше», конфликт открывает ``pull``.
2. Хвоста по полю нет. Поле применяется, если его штамп больше штампа
   последней записи поля в истории реплики (свои упакованные и чужие
   применённые операции — B14); иначе оно уже перезаписано.

Строки, которой у реплики нет, это не касается: чужая операция создаёт её
целиком.

**Точки расширения** — следующие задачи секции C подключаются отдельными
модулями пакета ``sync_service``, не трогая ядро:

| Вид | Задача | Модуль | Что модуль экспортирует |
|---|---|---|---|
| ``text`` (``text_columns`` реестра) | CLO-019 | ``text_merge`` | ``resolve(clash: FieldClash) -> Resolution`` |
| ``status`` (``task.status``, ``task.checked_out_*``) | CLO-020 | ``status_merge`` | ``resolve(clash: FieldClash) -> Resolution`` |
| ``edge`` (удаление ребра против его же правки) | CLO-021 | ``set_merge`` | ``resolve_entity(session, ctx, clash: EntityClash) -> bool`` |
| ``delete_edit`` (удаление против правки, FK на удалённое) | CLO-022 | ``tombstone`` | ``resolve_entity(session, ctx, clash: EntityClash) -> bool`` |
| ``key_merge`` (совпал натуральный ключ при вставке) | CLO-023 | ``keys`` | ``resolve_key(session, ctx, clash: KeyClash) -> bool`` |

Модуль ищется импортом по имени (``STRATEGIES``); нет модуля —
``MergeRequiredError`` с номером задачи. ``check`` зовёт тот же поиск до
первой записи pull'а, так что неразобранный случай не оставляет БД
наполовину; исключение — ``key_merge``: совпадение ключа видно только
запросом при вставке, и его граница срабатывает внутри транзакции
changeset'а (она откатывается целиком). ``resolve_entity``/``resolve_key``
возвращают ``True``, если чужую операцию дальше применять как обычно.
"""

from __future__ import annotations

import functools
import importlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import delete, insert, select
from ulid import ULID

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.hlc import Hlc, clock_for
from cod_doc.infra.models import Base, SyncOpModel
from cod_doc.infra.models.sync import SYNC_CONFLICT_KINDS
from cod_doc.services.sync_service import _local
from cod_doc.services.sync_service.changeset import FieldValue, canonical_json
from cod_doc.services.sync_service.errors import MergeRequiredError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from types import ModuleType

    from sqlalchemy import Table
    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service.changeset import Op

__all__ = [
    "DELETE_EDIT",
    "EDGE",
    "FIELD",
    "KEY_MERGE",
    "STATUS",
    "STRATEGIES",
    "TEXT",
    "ConflictSpec",
    "Context",
    "EntityClash",
    "FieldClash",
    "KeyClash",
    "Outcome",
    "Resolution",
    "check",
    "classify",
    "fields_to_apply",
    "fk_uids",
    "lww",
]

#: Штамп записи: ``(hlc, op_id)``.
type Stamp = tuple[str, str]

FIELD: Final = "field"
TEXT: Final = "text"
STATUS: Final = "status"
EDGE: Final = "edge"
DELETE_EDIT: Final = "delete_edit"
KEY_MERGE: Final = "key_merge"

#: Вид случая → (задача, модуль-стратегия в ``cod_doc.services.sync_service``).
#: ``field`` — встроенный LWW (``lww``), модуля у него нет.
STRATEGIES: Final[Mapping[str, tuple[str, str]]] = {
    TEXT: ("CLO-019", "text_merge"),
    STATUS: ("CLO-020", "status_merge"),
    EDGE: ("CLO-021", "set_merge"),
    DELETE_EDIT: ("CLO-022", "tombstone"),
    KEY_MERGE: ("CLO-023", "keys"),
}
#: ``checked_out_*`` идут той же стратегией: двойной офлайн-checkout виден только
#: по ним — ``status`` у двух захватов одинаковый и поглощается (CLO-020, Q-011).
_STATUS_FIELDS: Final = frozenset(
    {("task", "status"), ("task", "checked_out_by"), ("task", "checked_out_at")}
)
#: Виды конфликта про одно поле; у остальных ``field`` пуст.
_FIELD_CONFLICTS: Final = frozenset({FIELD, TEXT, STATUS})
_UPSERT: Final = "upsert"
_DELETE: Final = "delete"
#: Порог ``IN (...)``: SQLite старше 3.32 держит 999 параметров.
_CHUNK: Final = 500


# --- значения ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConflictSpec:
    """Что открыть в ``sync_conflict``: сущность, поле, вид и три значения (§3.7).

    Значения — в форме changeset'а (§10.4). Производит слияние, записывает
    ``conflicts.open_conflict`` в транзакции применения changeset'а.
    """

    entity_kind: str
    entity_uid: str
    #: ``None`` — у ``delete_edit`` и склейки ключей.
    field: str | None
    kind: str
    ours: FieldValue
    theirs: FieldValue
    base: FieldValue
    #: ``op_id`` чужой операции.
    theirs_op: str

    def __post_init__(self) -> None:
        if self.kind not in SYNC_CONFLICT_KINDS:
            msg = f"вид конфликта {self.kind!r} не из {sorted(SYNC_CONFLICT_KINDS)}"
            raise ValueError(msg)
        if (self.field is None) == (self.kind in _FIELD_CONFLICTS):
            need = "обязательно" if self.field is None else "лишнее"
            msg = f"конфликт {self.kind!r}: поле {need}"
            raise ValueError(msg)


class Outcome(StrEnum):
    THEIRS = "theirs"
    OURS = "ours"
    MERGED = "merged"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class Resolution:
    """Исход стратегии поля."""

    outcome: Outcome
    #: Сведённое значение (``merged``).
    value: FieldValue = None
    #: Вид конфликта (``conflict``): ``text``, ``status``…
    conflict_kind: str | None = None


@dataclass(frozen=True, slots=True)
class FieldClash:
    """Одно поле одной сущности записано и хвостом, и чужой операцией."""

    table: str
    entity_uid: str
    column: str
    ours: FieldValue
    theirs: FieldValue
    #: ``base`` первой своей неупакованной записи поля — общий предок с remote —
    #: и ``base`` чужой операции (для 3-way merge).
    ours_base: FieldValue
    theirs_base: FieldValue
    ours_stamp: Stamp
    theirs_stamp: Stamp
    theirs_op: str


@dataclass(frozen=True, slots=True)
class EntityClash:
    """Удаление с одной стороны против правки или ссылки с другой."""

    kind: str
    table: str
    entity_uid: str
    theirs: Op
    #: Свои неупакованные операции, с которыми пересеклась чужая.
    ours: tuple[Op, ...]


@dataclass(frozen=True, slots=True)
class KeyClash:
    """Чужая вставка совпала натуральным ключом со своей строкой другого uid."""

    table: str
    key: tuple[str, ...]
    theirs: Op
    ours_uid: str


@dataclass
class Context:
    """Состояние слияния одного pull'а: кто сливает и что получилось."""

    project_id: int
    replica_uid: str
    author: str
    #: Конфликты к открытию — ``pull`` пишет их в транзакции changeset'а.
    conflicts: list[ConflictSpec] = field(default_factory=list)
    #: Поле ``(entity_uid, column)`` → конфликт, который оно открыло в текущем
    #: changeset'е. Решает последняя чужая запись поля, а не промежуточная.
    by_field: dict[tuple[str, str], ConflictSpec] = field(default_factory=dict)

    def drain(self) -> list[ConflictSpec]:
        out, self.conflicts = self.conflicts, []
        self.by_field = {}
        return out

    def retract(self, entity_uid: str, column: str) -> None:
        """Чужой changeset снова пишет поле: конфликт его прошлой записи снимается.

        Иначе промежуточное состояние чужой истории (``cancelled`` по пути в
        ``todo``, checkout со следующим release) давало бы конфликт только той
        реплике, которая сливает чужую историю по шагам, — а реплика, у которой
        та же история в своём хвосте, видит лишь итог.
        """
        spec = self.by_field.pop((entity_uid, column), None)
        if spec is None:
            return
        self.conflicts = [c for c in self.conflicts if c is not spec]
        self.by_field = {k: v for k, v in self.by_field.items() if v is not spec}

    def add(self, column: str, spec: ConflictSpec) -> None:
        """Конфликт про сущность (без поля) — один на сущность и вид."""
        if spec.field is None:
            for c in self.conflicts:
                if (c.entity_uid, c.kind, c.field) == (spec.entity_uid, spec.kind, None):
                    self.by_field[(spec.entity_uid, column)] = c
                    return
        self.conflicts.append(spec)
        self.by_field[(spec.entity_uid, column)] = spec


type FieldStrategy = Callable[[FieldClash], Resolution]


class _FieldPlugin(Protocol):
    def resolve(self, clash: FieldClash) -> Resolution: ...


class _EntityPlugin(Protocol):
    def resolve_entity(self, session: Session, ctx: Context, clash: EntityClash) -> bool: ...


class _KeyPlugin(Protocol):
    def resolve_key(self, session: Session, ctx: Context, clash: KeyClash) -> bool: ...


# --- диспетчер -----------------------------------------------------------------------


def classify(table: str, column: str) -> str:
    """Вид поля: ``text`` (``text_columns`` реестра), ``status``, иначе ``field``."""
    if (table, column) in _STATUS_FIELDS:
        return STATUS
    spec = reg.REGISTRY.get(table)
    if spec is not None and column in spec.text_columns:
        return TEXT
    return FIELD


def _plugin(module: str) -> ModuleType | None:
    """Модуль стратегии; ``None`` — его ещё нет (задача не сделана).

    Отсутствие засчитывается только для самого модуля: ``ImportError``
    внутри существующего модуля — ошибка, а не «стратегии нет».
    """
    name = f"cod_doc.services.sync_service.{module}"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise


def _strategy_module(kind: str) -> ModuleType:
    task, module = STRATEGIES[kind]
    found = _plugin(module)
    if found is None:
        msg = f"случай {kind!r}: стратегии ещё нет"
        raise MergeRequiredError(msg, task=task)
    return found


def require(kind: str) -> None:
    """Граница: стратегии вида ``kind`` нет — ``MergeRequiredError`` с её задачей."""
    if kind != FIELD:
        _strategy_module(kind)


def field_strategy(kind: str) -> FieldStrategy:
    if kind == FIELD:
        return lww
    return cast("_FieldPlugin", _strategy_module(kind)).resolve


def lww(clash: FieldClash) -> Resolution:
    """Встроенная стратегия скалярного поля: больший штамп ``(hlc, op_id)`` (§3.7)."""
    if clash.theirs_stamp > clash.ours_stamp:
        return Resolution(Outcome.THEIRS)
    return Resolution(Outcome.OURS)


# --- метаданные ----------------------------------------------------------------------


@functools.cache
def _info(table: str) -> cap.TableInfo | None:
    import cod_doc.infra.models.link_suggestions  # noqa: F401 — ленивая модель

    for mapper in Base.registry.mappers:
        if cast("Table", mapper.local_table).name == table:
            return cap.table_info(mapper)
    return None


def fk_uids(op: Op) -> set[str]:
    """uid, на которые ссылаются FK-поля операции (``base`` у удаления)."""
    info = _info(op.entity_kind) if op.entity_kind in cap.CAPTURED_TABLES else None
    if info is None:
        return set()
    refs = {*info.fk_targets, *info.polymorphic}
    payload = op.fields if op.kind == _UPSERT else op.base
    return {str(v) for c, v in payload.items() if c in refs and isinstance(v, str)}


def _entity_kind(table: str, *, same_entity: bool) -> str:
    """Удаление ребра против его же правки — ``edge``; иначе ``delete_edit``."""
    if same_entity and reg.spec_for(table).deterministic_uid is not None:
        return EDGE
    return DELETE_EDIT


def _equal(a: object, b: object) -> bool:
    return canonical_json(cast("FieldValue", a)) == canonical_json(cast("FieldValue", b))


def _stamp(op: Op) -> Stamp:
    return (op.hlc, op.op_id)


# --- проверка до записи -----------------------------------------------------------


def _latest(tail: Sequence[Op], column: str) -> Op | None:
    found = [op for op in tail if op.kind == _UPSERT and column in op.fields]
    return max(found, key=_stamp) if found else None


def _earliest_base(tail: Sequence[Op], column: str) -> FieldValue:
    """``base`` первой своей записи поля в хвосте — значение до офлайн-правок."""
    found = [op for op in tail if op.kind == _UPSERT and column in op.fields]
    return min(found, key=_stamp).base.get(column) if found else None


def check(foreign: Sequence[Op], tail: Sequence[Op]) -> None:
    """До первой записи pull'а: все ли случаи расхождения сводятся.

    ``foreign`` — операции чужих changeset'ов pull'а, ``tail`` — свой
    неупакованный хвост. Случай без стратегии — ``MergeRequiredError`` с
    задачей.
    """
    if not tail:
        return
    by_entity: dict[tuple[str, str], list[Op]] = {}
    for op in tail:
        by_entity.setdefault((op.entity_kind, op.entity_uid), []).append(op)
    deleted = {op.entity_uid for op in tail if op.kind == _DELETE}
    referenced = set().union(*(fk_uids(op) for op in tail if op.kind == _UPSERT))
    for op in foreign:
        ours = by_entity.get((op.entity_kind, op.entity_uid), [])
        if op.kind == _DELETE:
            clash = any(o.kind == _UPSERT for o in ours)
            if clash or op.entity_uid in referenced:
                require(_entity_kind(op.entity_kind, same_entity=clash))
            continue
        if any(o.kind == _DELETE for o in ours) or fk_uids(op) & deleted:
            require(_entity_kind(op.entity_kind, same_entity=bool(ours)))
            continue
        for column, theirs in op.fields.items():
            mine = _latest(ours, column)
            if mine is None:
                continue
            if not _equal(mine.fields[column], theirs):
                require(classify(op.entity_kind, column))


# --- применение ------------------------------------------------------------------


def _entity_rows(session: Session, project_id: int, op: Op) -> list[SyncOpModel]:
    return list(
        session.scalars(
            select(SyncOpModel)
            .where(
                SyncOpModel.project_id == project_id,
                SyncOpModel.entity_kind == op.entity_kind,
                SyncOpModel.entity_uid == op.entity_uid,
            )
            .order_by(SyncOpModel.hlc, SyncOpModel.op_id)
        )
    )


def _dumps(payload: Mapping[str, object]) -> str:
    """Форма ``sync_op.fields``/``base``, как у захвата: значения уже JSON."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload(row: SyncOpModel, column: str) -> dict[str, object]:
    raw = json.loads(getattr(row, column))
    return raw if isinstance(raw, dict) else {}


def _trim(session: Session, rows: Sequence[SyncOpModel], column: str) -> None:
    """Убрать поле из неупакованных операций; операция без полей уходит целиком."""
    for row in rows:
        fields = _payload(row, "fields")
        if column not in fields:
            continue
        fields.pop(column)
        if not fields and row.kind == _UPSERT:
            session.execute(delete(SyncOpModel).where(SyncOpModel.op_id == row.op_id))
            continue
        base = _payload(row, "base")
        base.pop(column, None)
        row.fields = _dumps(fields)
        row.base = _dumps(base)
    session.flush()


def _append(
    session: Session,
    ctx: Context,
    op: Op,
    fields: dict[str, FieldValue],
    base: Mapping[str, object],
) -> None:
    """Новая операция хвоста со штампом больше чужого: её увезёт следующий push."""
    clock = clock_for(session, ctx.replica_uid)
    clock.observe(Hlc.parse(op.hlc))
    session.execute(
        insert(cast("Table", SyncOpModel.__table__)),
        [
            {
                "op_id": str(ULID()),
                "project_id": ctx.project_id,
                "replica_uid": ctx.replica_uid,
                "hlc": str(clock.now()),
                "entity_kind": op.entity_kind,
                "entity_uid": op.entity_uid,
                "kind": _UPSERT,
                "fields": _dumps(fields),
                "base": _dumps(base),
                "author": ctx.author,
                "changeset": None,
            }
        ],
    )


def _history_stamp(rows: Sequence[SyncOpModel], column: str) -> Stamp | None:
    stamps = [
        (row.hlc, row.op_id)
        for row in rows
        if row.changeset is not None and row.kind == _UPSERT and column in _payload(row, "fields")
    ]
    return max(stamps) if stamps else None


def fields_to_apply(session: Session, ctx: Context, op: Op) -> dict[str, FieldValue]:
    """Поля чужого upsert'а над существующей строкой, которые надо записать (§3.7)."""
    rows = _entity_rows(session, ctx.project_id, op)
    pending = [row for row in rows if row.changeset is None]
    tail = [_local.op_of(row) for row in pending]
    out: dict[str, FieldValue] = {}
    merged: dict[str, FieldValue] = {}
    merged_base: dict[str, object] = {}
    theirs_stamp = _stamp(op)
    for column, theirs in op.fields.items():
        mine = _latest(tail, column)
        if mine is None:
            seen = _history_stamp(rows, column)
            if seen is None or theirs_stamp > seen:
                out[column] = theirs
            continue
        ctx.retract(op.entity_uid, column)
        if _equal(mine.fields[column], theirs):
            _trim(session, pending, column)  # одинаковое значение: операции нет
            out[column] = theirs
            continue
        clash = FieldClash(
            table=op.entity_kind,
            entity_uid=op.entity_uid,
            column=column,
            ours=mine.fields[column],
            theirs=theirs,
            ours_base=_earliest_base(tail, column),
            theirs_base=op.base.get(column),
            ours_stamp=_stamp(mine),
            theirs_stamp=theirs_stamp,
            theirs_op=op.op_id,
        )
        result = field_strategy(classify(op.entity_kind, column))(clash)
        if result.outcome is Outcome.THEIRS:
            _trim(session, pending, column)
            out[column] = theirs
        elif result.outcome is Outcome.MERGED:
            _trim(session, pending, column)
            out[column] = result.value
            merged[column] = result.value
            merged_base[column] = theirs
        elif result.outcome is Outcome.CONFLICT:
            kind = result.conflict_kind or classify(op.entity_kind, column)
            ctx.add(
                column,
                ConflictSpec(
                    entity_kind=op.entity_kind,
                    entity_uid=op.entity_uid,
                    # Конфликт про сущность (``double_checkout``) — без поля.
                    field=column if kind in _FIELD_CONFLICTS else None,
                    kind=kind,
                    ours=clash.ours,
                    theirs=theirs,
                    base=clash.theirs_base,
                    theirs_op=op.op_id,
                ),
            )
    if merged:
        _append(session, ctx, op, merged, merged_base)
    return out


def _pending_ops(session: Session, ctx: Context) -> list[Op]:
    return _local.pending_ops(session, ctx.project_id)


def allow(session: Session, ctx: Context, op: Op) -> bool:
    """Удаление против правки или ссылки (§3.7, Q-008): решает стратегия вида.

    Без пересечения — ``True``. Стратегии нет — ``MergeRequiredError``
    (``check`` ловит это до первой записи; здесь — страховка).
    """
    tail = _pending_ops(session, ctx)
    if not tail:
        return True
    same = tuple(
        o for o in tail if (o.entity_kind, o.entity_uid) == (op.entity_kind, op.entity_uid)
    )
    if op.kind == _DELETE:
        hits = tuple(o for o in tail if o.kind == _UPSERT and op.entity_uid in fk_uids(o))
        ours = tuple(o for o in same if o.kind == _UPSERT) + hits
        same_entity = any(o.kind == _UPSERT for o in same)
    else:
        deleted = {o.entity_uid: o for o in tail if o.kind == _DELETE}
        ours = tuple(o for o in same if o.kind == _DELETE) + tuple(
            deleted[u] for u in sorted(fk_uids(op) & deleted.keys())
        )
        same_entity = bool(same)
    if not ours:
        return True
    kind = _entity_kind(op.entity_kind, same_entity=same_entity)
    plugin = cast("_EntityPlugin", _strategy_module(kind))
    return plugin.resolve_entity(
        session, ctx, EntityClash(kind, op.entity_kind, op.entity_uid, op, ours)
    )


def key_collision(
    session: Session, ctx: Context, op: Op, key: tuple[str, ...], ours_uid: str
) -> bool:
    """Чужая вставка совпала натуральным ключом со своей строкой (Q-007): CLO-023."""
    plugin = cast("_KeyPlugin", _strategy_module(KEY_MERGE))
    return plugin.resolve_key(session, ctx, KeyClash(op.entity_kind, key, op, ours_uid))
