"""Захват операций в журнал ``sync_op`` (RFC 29 §3.4, контракт §10.3; CLO-006).

Хук unit-of-work на фабрике сессий: каждая запись в authored-данные через
``Session`` оставляет операцию ``upsert``/``delete`` с изменёнными
authored-полями (``fields``) и их прежними значениями (``base``). Правка
сервисов не нужна — вся запись и так идёт через ``Session`` (гейт ADO-208).
Журнал пишется всегда, даже без настроенного remote: хвост неупакованных
операций — то, что потом уедет push'ем.

Как устроено (решения там, где контракт §10.3 оставляет выбор):

1. **Слушатель на фабрике, не на классе ``Session``.** ``install`` вешает
   события на конкретный ``sessionmaker`` (зовёт ``infra/db.py::
   make_session_factory`` — единственная фабрика); голый ``Session(engine)``
   в тестах не захватывается случайно.
2. **``before_flush`` назначает uid, ``after_flush`` собирает операции.**
   В ``before_flush`` у новой строки нет ни ``row_id``, ни FK, проставленных
   через relationship; в ``after_flush`` они есть, а история атрибутов и
   ``session.new`` ещё в состоянии до flush (документированная гарантия
   SQLAlchemy). Обход — по ``flush_context.states``, а не по
   ``session.deleted``: там же каскадные удаления и delete-orphan, которых в
   ``session.deleted`` нет. Удалённые строки к этому моменту ушли из БД, но
   ещё лежат в identity map — родитель, удалённый тем же flush, читается
   оттуда.
3. **Строка целиком** (решения 3, 12, 17 реестра). Гейт ``row_is_local`` и
   ``withheld_columns`` получают все колонки строки плюс метку документа под
   ``sensitivity_key(колонка)`` для каждой ссылки из ``DOC_REFS``. Метку
   разрешает захват: документ, секция → документ, ``doc_key`` в проекте,
   ``to_ref`` (``doc_key#anchor``), полиморфный ``entity_row_id``, ревизия →
   её сущность. Не нашлась — метки нет, и реестр не выпускает строку
   (fail-closed).
4. **FK — uid конца**, ключ — имя колонки: ``{"section_id": "0192…"}``. Имя
   колонки, а не таблицы (RFC §3.3 пишет ``{"plan_section": …}``), потому что у
   рёбер два FK на одну таблицу (``from_task_id``/``to_task_id``). Конец без
   uid (строка, вставленная мимо хука) — колонка не уезжает, в лог идёт
   предупреждение: сериализовать локальный ``row_id`` нельзя.
5. **Автор.** ``session.info["author"]`` ставят сервисы через
   ``set_author`` — его зовут ``activity_service.emit`` и
   ``revision_service.write``, через которые проходит каждый write-путь
   (ADO-040). Автор обычно известен только после первого flush (ревизии нужен
   ``row_id``), поэтому операции копятся в ``session.info`` и пишутся в
   ``before_commit`` корневой транзакции; там же последний flush, чтобы хвост
   не потерялся. Нет автора — ``system:unknown`` и предупреждение (§10.3).
6. **Откат.** Операция помнит транзакцию, в которой захвачена. Откат
   SAVEPOINT выбрасывает операции этого SAVEPOINT и вложенных; откат или
   закрытие корневой — все. Иначе ``task_create_many`` с конфликтом на одной
   задаче записал бы операцию задачи, которой нет.
7. **Пустая операция не пишется.** Изменились только ``local``-колонки (или
   ``uid``, который хук только что назначил) — операции нет. Строка, ставшая
   local (документ повысили до ``restricted``), операции не даёт, а в лог идёт
   предупреждение «на remote осталась история» (Q-015). Исключение —
   документ, который уже уезжал: он даёт операцию отзыва, а неотправленный
   хвост его тела вычищается (решение 20 реестра, ``sync_withdraw``).
   Строка, переставшая быть local, уезжает upsert'ом целиком; документ —
   вместе с телом, ссылками на него и ревизиями, которых remote не видел.
8. **Порядок.** Внутри flush upsert'ы идут в порядке зависимостей таблиц
   (родитель раньше ребёнка), удаления — в обратном и после upsert'ов: так их
   можно применять по возрастанию HLC.
9. **Каскад внутри БД** (``ON DELETE CASCADE`` без ORM-каскада) операций
   детей не даёт: на другой реплике тот же каскад сработает от удаления
   родителя.
10. **Журнал** (``JOURNAL_IDS`` реестра, CLO-024). Ревизия даёт одну операцию
    ``upsert`` на вставку: адрес — ``revision_id``, поля — уезжающие колонки
    без него. Правка и удаление строки журнала операций не дают: журнал
    дописывается, а удаляет его только каскад удалённого проекта. Ревизия
    уезжает, только если её сущность уезжает целиком (вердикт под
    ``ENTITY_SHIPS_KEY``, решение 19 реестра); из JSON-``diff`` вычищаются
    ключи local-колонок сущности (``sanitize_diff``) — и в операции, и в
    дайджесте, так что реплики сходятся.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, cast

from sqlalchemy import event, func, insert, select, text, update
from sqlalchemy.orm import Mapper, Session, attributes
from sqlalchemy.orm.base import NO_VALUE
from sqlalchemy.orm.util import identity_key
from ulid import ULID

from cod_doc.domain.entities import ActorKind, EntityKind, actor_kind_for_author
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.hlc import clock_for, self_replica
from cod_doc.infra.models import Base, SyncOpModel

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from sqlalchemy import Connection, Table
    from sqlalchemy.orm import InstanceState, SessionTransaction, sessionmaker
    from sqlalchemy.orm.unitofwork import UOWTransaction

logger = logging.getLogger("cod_doc.infra.sync_capture")

#: Ключ ``session.info`` с автором транзакции (§10.3).
AUTHOR_KEY = "author"
UNKNOWN_AUTHOR = "system:unknown"
_PENDING_KEY = "cod_doc.sync_capture.pending"
_SUSPENDED_KEY = "cod_doc.sync_capture.suspended"

OpKind = Literal["upsert", "delete"]
_OP_KINDS = frozenset({"upsert", "delete"})
_UID = "uid"
_ROW_ID = "row_id"
_PROJECT_ID = "project_id"
_SENSITIVITY = "sensitivity"
_DOC_KINDS = frozenset({"document", "section"})

#: Таблицы под захватом: authored, derived с authored-колонками
#: (``link_suggestion.state``, Q-017) и journal из ``JOURNAL_IDS`` (CLO-024).
CAPTURED_TABLES: frozenset[str] = frozenset(
    t
    for t, spec in reg.REGISTRY.items()
    if spec.cls is reg.SyncClass.AUTHORED or spec.authored_columns or t in reg.JOURNAL_IDS
)
_SHIPPED_CLASSES = frozenset({reg.SyncClass.AUTHORED, reg.SyncClass.JOURNAL})

#: Как разрешить метку документа для каждой ссылки ``DOC_REFS`` захватываемых
#: таблиц: ``document``/``section`` — FK, ``doc_key`` — ключ документа в проекте,
#: ``to_ref`` — ``doc_key#anchor`` при ``to_kind`` документа, ``entity_kind`` —
#: полиморфный ``row_id``, ``revision`` — ревизия → её сущность. Полноту
#: относительно ``DOC_REFS`` сверяет ``tests/infra/test_sync_capture.py``.
DOC_REF_TARGETS: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("section", "document_id"): "document",
        ("doc_comment", "document_id"): "document",
        ("doc_comment", "section_id"): "section",
        ("document_tag", "document_id"): "document",
        ("plan", "parent_doc_id"): "document",
        ("plan", "completed_log_id"): "document",
        ("plan_section", "doc_id"): "document",
        ("module", "spec_doc_id"): "document",
        ("scenario", "document_id"): "document",
        ("scenario", "doc_key"): "doc_key",
        ("open_question", "source_doc_key"): "doc_key",
        ("open_question_link", "to_ref"): "to_ref",
        ("story_link", "to_ref"): "to_ref",
        ("scenario_link", "to_ref"): "to_ref",
        ("external_ref", "entity_row_id"): "entity_kind",
        ("approval_doc_revision_link", "revision_id"): "revision",
        ("link_suggestion", "from_section_id"): "section",
        ("link_suggestion", "to_section_id"): "section",
        ("link_suggestion", "to_doc_key"): "doc_key",
        ("revision", "entity_id"): "entity_kind",
    }
)


# --- метаданные таблиц --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TableInfo:
    """Что захвату нужно знать о таблице; строится один раз на маппер."""

    table: str
    #: (колонка, атрибут модели) — все колонки строки.
    columns: tuple[tuple[str, str], ...]
    #: Колонки, которые уезжают (``column_class == AUTHORED``), без ``uid``.
    shipped: frozenset[str]
    #: FK (настоящие и логические) → таблица конца.
    fk_targets: Mapping[str, str]
    #: Логические FK (без ограничения в БД): висячее значение = «ссылки нет».
    logical: frozenset[str]
    #: Полиморфные FK → колонка-дискриминатор (``POLYMORPHIC_FKS``).
    polymorphic: Mapping[str, str]
    has_uid: bool
    #: Колонки, где ``NULL`` — значение («ссылки нет»), а не «ещё не проставлено».
    nullable: frozenset[str]
    #: Ссылка, через которую ищется проект строки без ``project_id``.
    parent: tuple[str, str] | None
    #: Колонка глобального id journal-таблицы (``JOURNAL_IDS``): адрес операции.
    identity: str | None = None

    def attr(self, column: str) -> str:
        return dict(self.columns)[column]


def _table(mapper: Mapper[object]) -> Table:
    """Таблица маппера; у моделей проекта это всегда ``Table`` (без наследования)."""
    return cast("Table", mapper.local_table)


def _table_info(mapper: Mapper[object]) -> TableInfo | None:
    table = _table(mapper)
    name = table.name
    if name not in CAPTURED_TABLES:
        return None
    spec = reg.spec_for(name)
    identity = reg.JOURNAL_IDS.get(name)
    columns = tuple((c.name, mapper.get_property_by_column(c).key) for c in table.columns)
    shipped = frozenset(
        c.name
        for c in table.columns
        if c.name not in {_UID, identity} and reg.column_class(name, c.name) in _SHIPPED_CLASSES
    )
    fk_targets: dict[str, str] = {fk.parent.name: fk.column.table.name for fk in table.foreign_keys}
    fk_targets.update(spec.logical_fks)
    polymorphic = {col: disc for (t, col), disc in reg.POLYMORPHIC_FKS.items() if t == name}
    parent: tuple[str, str] | None = None
    if _PROJECT_ID not in table.c and name != "project":
        # Не-nullable ссылка надёжнее: у неё всегда есть конец.
        candidates = sorted(fk_targets, key=lambda c: (table.c[c].nullable, c))
        parent = (candidates[0], fk_targets[candidates[0]]) if candidates else None
    return TableInfo(
        table=name,
        columns=columns,
        shipped=shipped,
        fk_targets=MappingProxyType(fk_targets),
        logical=frozenset(spec.logical_fks),
        polymorphic=MappingProxyType(polymorphic),
        has_uid=_UID in table.c,
        nullable=frozenset(c.name for c in table.columns if c.nullable),
        parent=parent,
        identity=identity,
    )


_INFO_CACHE: dict[Mapper[object], TableInfo | None] = {}


def table_info(mapper: Mapper[object]) -> TableInfo | None:
    try:
        return _INFO_CACHE[mapper]
    except KeyError:
        info = _INFO_CACHE[mapper] = _table_info(mapper)
        return info


_MAPPERS: dict[str, Mapper[object]] = {}


def _mapper_for(table: str) -> Mapper[object] | None:
    """Маппер таблицы; кэш перестраивается на промахе — ``link_suggestion``
    регистрируется лениво (решение 1 реестра)."""
    mapper = _MAPPERS.get(table)
    if mapper is None:
        for m in Base.registry.mappers:
            _MAPPERS.setdefault(_table(m).name, m)
        mapper = _MAPPERS.get(table)
    return mapper


def _table_order() -> dict[str, int]:
    return {t.name: i for i, t in enumerate(Base.metadata.sorted_tables)}


# --- чтение связанных строк ---------------------------------------------------


class SyncCaptureError(RuntimeError):
    """Операцию над authored-строкой построить нельзя — транзакция не коммитится.

    Fail-closed (RFC 29 §10.10, ``SYNC-001``: запись в authored мимо журнала).
    Запись без операции молча развела бы реплики; урезанная операция — тоже.
    Сообщение называет только таблицу и колонку, без значений.
    """

    code = "SYNC-001"

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code} {detail}")


class _Unresolvable(Exception):
    """Конец ссылки без uid: колонку нельзя сериализовать."""


class _Lookup:
    """Чтение строк по ``row_id`` в пределах одного flush: identity map, затем БД.

    Identity map первым: строка, удалённая тем же flush, из БД уже ушла, а
    объект ещё там. Кэш живёт один flush — uid за это время не меняется.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._conn: Connection | None = None
        self._rows: dict[tuple[str, object], Mapping[str, object] | None] = {}
        self._doc_keys: dict[tuple[object, object], object] = {}

    @property
    def conn(self) -> Connection:
        if self._conn is None:
            self._conn = self.session.connection()
        return self._conn

    def row(self, table: str, row_id: object) -> Mapping[str, object] | None:
        key = (table, row_id)
        if key in self._rows:
            return self._rows[key]
        found = self._from_identity_map(table, row_id)
        mapper = _mapper_for(table)
        if mapper is not None and (found is None or len(found) < len(_table(mapper).c)):
            # Нет в identity map или атрибуты не загружены: добираем из БД;
            # загруженное в объекте новее (или строка уже удалена этим flush).
            tbl = _table(mapper)
            first = self.conn.execute(select(tbl).where(tbl.c.row_id == row_id)).mappings().first()
            if first is not None:
                found = {**dict(first), **(found or {})}
        self._rows[key] = found
        return found

    def _from_identity_map(self, table: str, row_id: object) -> Mapping[str, object] | None:
        mapper = _mapper_for(table)
        if mapper is None:
            return None
        obj = self.session.identity_map.get(identity_key(mapper.class_, row_id))
        if obj is None:
            return None
        state = attributes.instance_state(obj)
        out: dict[str, object] = {}
        for column in _table(mapper).columns:
            value = _current(state, mapper.get_property_by_column(column).key)
            if value is not NO_VALUE:
                out[column.name] = value
        return out

    def uid(self, table: str, row_id: object) -> str:
        row = self.row(table, row_id)
        value = row.get(_UID) if row is not None else None
        if not isinstance(value, str) or not value:
            raise _Unresolvable(f"конец в {table} без uid")
        return value

    def doc_sensitivity_by_key(self, project_id: object, doc_key: object) -> object:
        key = (project_id, doc_key)
        if key not in self._doc_keys:
            mapper = _mapper_for("document")
            assert mapper is not None
            tbl = _table(mapper)
            self._doc_keys[key] = self.conn.execute(
                select(tbl.c.sensitivity).where(
                    tbl.c.project_id == project_id, tbl.c.doc_key == doc_key
                )
            ).scalar_one_or_none()
        return self._doc_keys[key]

    def project_id(self, table: str, row: Mapping[str, object], depth: int = 0) -> object:
        if table == "project":
            return row.get(_ROW_ID)
        if _PROJECT_ID in row:
            return row[_PROJECT_ID]
        mapper = _mapper_for(table)
        info = table_info(mapper) if mapper is not None else None
        if info is None or info.parent is None or depth > len(CAPTURED_TABLES):
            return None
        column, target = info.parent
        value = row.get(column)
        parent = self.row(target, value) if value is not None else None
        return self.project_id(target, parent, depth + 1) if parent is not None else None


def _current(state: InstanceState[object], key: str) -> object:
    """Значение атрибута без загрузки из БД; ``NO_VALUE`` — не загружено."""
    return state.dict.get(key, NO_VALUE)


def _before(state: InstanceState[object], key: str) -> object:
    """Значение до flush: исходное у изменённого атрибута, иначе текущее."""
    if key in state.committed_state:
        return state.committed_state[key]
    return _current(state, key)


# --- метки документов ---------------------------------------------------------


def _doc_label(lookup: _Lookup, row_id: object) -> object:
    doc = lookup.row("document", row_id)
    return doc.get(_SENSITIVITY) if doc is not None else None


def _section_label(lookup: _Lookup, row_id: object) -> object:
    section = lookup.row("section", row_id)
    if section is None or section.get("document_id") is None:
        return None
    return _doc_label(lookup, section["document_id"])


def _entity_label(lookup: _Lookup, kind: object, row_id: object) -> object:
    if kind == EntityKind.DOCUMENT.value:
        return _doc_label(lookup, row_id)
    if kind == EntityKind.SECTION.value:
        return _section_label(lookup, row_id)
    return None


def _label(
    lookup: _Lookup, target: str, row: Mapping[str, object], column: str, project_id: object
) -> object:
    value = row.get(column)
    if value is None:
        return None
    if target == "document":
        return _doc_label(lookup, value)
    if target == "section":
        return _section_label(lookup, value)
    if target == "doc_key":
        return lookup.doc_sensitivity_by_key(project_id, value)
    if target == "to_ref":
        # Не-документный вид реестр снимает сам; документный и незнакомый —
        # ``doc_key`` до ``#``.
        return lookup.doc_sensitivity_by_key(project_id, str(value).partition("#")[0])
    if target == "entity_kind":
        return _entity_label(lookup, row.get("entity_kind"), value)
    if target == "revision":
        rev_mapper = _mapper_for("revision")
        assert rev_mapper is not None
        tbl = _table(rev_mapper)
        hit = lookup.conn.execute(
            select(tbl.c.entity_kind, tbl.c.entity_id).where(tbl.c.revision_id == value)
        ).first()
        return _entity_label(lookup, hit[0], hit[1]) if hit is not None else None
    msg = f"sync_capture: неизвестная цель метки {target!r}"
    raise AssertionError(msg)


def gating_row(
    lookup: _Lookup, table: str, row: Mapping[str, object], project_id: object
) -> dict[str, object]:
    """Строка для гейта реестра: все колонки плюс метки документов ссылок.

    У ревизии — ещё вердикт по её сущности (``ENTITY_SHIPS_KEY``).
    """
    out = dict(row)
    for ref in reg.DOC_REFS.get(table, ()):
        label = _label(lookup, DOC_REF_TARGETS[(table, ref.column)], row, ref.column, project_id)
        if label is not None:
            out[reg.sensitivity_key(ref.column)] = label
    if table in reg.JOURNAL_IDS:
        out[reg.ENTITY_SHIPS_KEY] = _entity_ships(lookup, row)
    return out


def _journal_entity_table(row: Mapping[str, object]) -> str | None:
    try:
        kind = EntityKind(str(row.get("entity_kind")))
    except ValueError:
        return None
    table = reg.ENTITY_KIND_TABLES.get(kind)
    return table if table in reg.UID_TABLES else None


def _entity_ships(lookup: _Lookup, row: Mapping[str, object]) -> bool:
    """Сущность ревизии уезжает целиком: строка есть, гейт выпускает, ничего не придержано.

    Fail-closed: сущности нет (удалена), вид без uid — ``False``.
    """
    table = _journal_entity_table(row)
    entity_id = row.get("entity_id")
    entity = lookup.row(table, entity_id) if table is not None and entity_id is not None else None
    if table is None or entity is None:
        return False
    gate = gating_row(lookup, table, entity, lookup.project_id(table, entity))
    return _ships(table, gate) and not reg.withheld_columns(table, gate)


#: Ключи значений поля в diff вида ``{"field": f, "old": …, "new": …}``.
_DIFF_VALUE_KEYS = frozenset({"old", "new", "value", "from", "to", "before", "after"})


def _local_columns(table: str) -> frozenset[str]:
    mapper = _mapper_for(table)
    if mapper is None:
        return frozenset()
    return frozenset(
        c.name
        for c in _table(mapper).columns
        if reg.column_class(table, c.name) is reg.SyncClass.LOCAL
    )


def _strip(node: object, local: frozenset[str]) -> tuple[object, bool]:
    if isinstance(node, list):
        items = [_strip(item, local) for item in node]
        return [v for v, _ in items], any(c for _, c in items)
    if not isinstance(node, dict):
        return node, False
    hidden = node.get("field") in local
    out: dict[str, object] = {}
    changed = False
    for key, value in node.items():
        if key in local or (hidden and key in _DIFF_VALUE_KEYS):
            changed = True
            continue
        out[key], sub = _strip(value, local)
        changed = changed or sub
    return out, changed


def sanitize_diff(entity_kind: object, diff: object) -> object:
    """``diff`` ревизии без значений local-колонок её сущности (решение 19 реестра).

    JSON-объект или массив: убираются ключи — local-колонки таблицы сущности
    (``position``, ``projection_hash``, ``row_id``…), и значения у записи
    ``{"field": <local-колонка>, "old"/"new"/…}``. Остальное (unified diff
    тела, скаляр) — как есть: тело секции уезжает и так. Ничего не убрано —
    строка возвращается байт в байт; убрано — пересобирается JSON. Повторный
    вызов ничего не меняет, поэтому дайджест реплики-источника и
    реплики-приёмника совпадает.
    """
    if not isinstance(diff, str) or diff.lstrip()[:1] not in ("{", "["):
        return diff
    table = _journal_entity_table({"entity_kind": entity_kind})
    try:
        parsed = json.loads(diff)
    except json.JSONDecodeError:
        return diff
    stripped, changed = _strip(parsed, _local_columns(table) if table else frozenset())
    return json.dumps(stripped, ensure_ascii=False) if changed else diff


def _ships(table: str, row: Mapping[str, object]) -> bool:
    predicate = reg.spec_for(table).row_is_local
    return predicate is None or not predicate(row)


# --- сериализация ---------------------------------------------------------------


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, set | frozenset):
        return sorted(value, key=str)
    msg = f"sync_capture: не сериализуется {type(value).__name__}"
    raise TypeError(msg)


def _dumps(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload, default=_json_default, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _serialize(info: TableInfo, column: str, row: Mapping[str, object], lookup: _Lookup) -> object:
    value = row.get(column)
    if value is None:
        return None
    if info.identity is not None and column == "diff":
        return sanitize_diff(row.get("entity_kind"), value)
    if column in info.fk_targets:
        target = info.fk_targets[column]
        if column in info.logical and lookup.row(target, value) is None:
            return None  # висячая логическая ссылка (полку удалили мимо сервиса)
        return lookup.uid(target, value)
    if column in info.polymorphic:
        try:
            kind = EntityKind(str(row.get(info.polymorphic[column])))
        except ValueError as exc:
            raise _Unresolvable("незнакомый вид сущности") from exc
        poly_target = reg.ENTITY_KIND_TABLES.get(kind)
        if poly_target is None or poly_target not in reg.UID_TABLES:
            raise _Unresolvable("вид сущности без uid")
        if lookup.row(poly_target, value) is None:
            return None  # висячая полиморфная ссылка: FK нет, ссылаться не на что
        return lookup.uid(poly_target, value)
    return value


def _columns_payload(
    info: TableInfo,
    columns: frozenset[str],
    row: Mapping[str, object],
    lookup: _Lookup,
) -> dict[str, object]:
    out: dict[str, object] = {}
    for column in sorted(columns):
        if column not in row or row[column] is NO_VALUE:
            continue
        try:
            out[column] = _serialize(info, column, row, lookup)
        except _Unresolvable as exc:
            raise SyncCaptureError(f"{info.table}.{column}: {exc}") from None
    return out


def _edge_key(info: TableInfo, row: Mapping[str, object], lookup: _Lookup) -> dict[str, object]:
    columns = reg.spec_for(info.table).deterministic_uid or ()
    key: dict[str, object] = {}
    for column in columns:
        value = row.get(column)
        if value is None and column not in info.nullable:
            # До flush FK, выставленный через relationship, ещё пуст: uid
            # ребра с ``None`` вместо конца совпал бы у всех таких рёбер.
            raise _Unresolvable(f"{info.table}.{column}: ещё не проставлен")
        if column in info.fk_targets and value is not None:
            value = lookup.uid(info.fk_targets[column], value)
        key[column] = value
    return key


def _edge_uid(info: TableInfo, row: Mapping[str, object], lookup: _Lookup) -> str | None:
    """Детерминированный uid ребра; ``None`` — конец ещё не разрешим."""
    try:
        return reg.edge_uid(info.table, _edge_key(info, row, lookup))
    except _Unresolvable:
        return None


# --- операции -------------------------------------------------------------------


@dataclass(slots=True)
class PendingOp:
    """Операция, захваченная в транзакции и ещё не записанная в ``sync_op``."""

    op_id: str
    project_id: int
    entity_kind: str
    entity_uid: str
    kind: str
    fields: dict[str, object]
    base: dict[str, object]
    author: str | None
    tx: SessionTransaction | None = field(default=None, repr=False)
    #: Строка на момент захвата (row_id-FK) — для пересверки гейта на commit.
    row: dict[str, object] | None = field(default=None, repr=False)
    #: Операция отзыва (решение 20 реестра): гейт на commit её не выбрасывает —
    #: строка local по построению — и с соседними upsert'ами она не склеивается.
    withdraw: bool = False
    #: Первый upsert строки, переставшей быть local (``base`` пуст).
    promoted: bool = False


def _pending(session: Session) -> list[PendingOp]:
    pending: list[PendingOp] = session.info.setdefault(_PENDING_KEY, [])
    return pending


def pending_ops(session: Session) -> list[PendingOp]:
    """Захваченные, но ещё не записанные операции транзакции (для тестов)."""
    return list(session.info.get(_PENDING_KEY, ()))


def _current_tx(session: Session) -> SessionTransaction | None:
    return session.get_nested_transaction() or session.get_transaction()


def _is_suspended(session: Session) -> bool:
    return bool(session.info.get(_SUSPENDED_KEY, 0))


def _row_now(info: TableInfo, obj: object) -> dict[str, object]:
    """Текущая строка; незагруженное догружается (серверные дефолты новой строки)."""
    return {column: getattr(obj, key) for column, key in info.columns}


def _row_before(info: TableInfo, state: InstanceState[object]) -> dict[str, object]:
    return {column: _before(state, key) for column, key in info.columns}


def _changed(info: TableInfo, state: InstanceState[object]) -> frozenset[str]:
    out: set[str] = set()
    for column, key in info.columns:
        if column in info.shipped and state.attrs[key].history.has_changes():
            out.add(column)
    return frozenset(out)


def _new_op(
    session: Session,
    info: TableInfo,
    project_id: object,
    entity_uid: str,
    kind: OpKind,
    fields: dict[str, object],
    base: dict[str, object],
    row: Mapping[str, object],
) -> PendingOp:
    if not isinstance(project_id, int):
        raise SyncCaptureError(f"{info.table}: проект строки не найден")
    return PendingOp(
        op_id=str(ULID()),
        project_id=project_id,
        entity_kind=info.table,
        entity_uid=entity_uid,
        kind=kind,
        fields=fields,
        base=base,
        author=session.info.get(AUTHOR_KEY),
        tx=_current_tx(session),
        row=dict(row),
    )


def _entity_uid(info: TableInfo, row: Mapping[str, object], lookup: _Lookup) -> str | None:
    if info.has_uid or info.identity is not None:
        value = row.get(info.identity or _UID)
        return value if isinstance(value, str) and value else None
    return _edge_uid(info, row, lookup)


def _upsert_op(
    session: Session,
    info: TableInfo,
    state: InstanceState[object],
    *,
    is_new: bool,
    lookup: _Lookup,
) -> PendingOp | None:
    if info.identity is not None and not is_new:
        return None  # журнал дописывается: правка строки журнала не уезжает (решение 10)
    changed = info.shipped if is_new else _changed(info, state)
    if not changed:
        return None
    obj = state.obj()
    now = _row_now(info, obj)
    project_id = lookup.project_id(info.table, now)
    gate_now = gating_row(lookup, info.table, now, project_id)
    promoted = False
    if not is_new:
        before = {**now, **{c: v for c, v in _row_before(info, state).items() if c in changed}}
        ships_before = _ships(info.table, gating_row(lookup, info.table, before, project_id))
        if not _ships(info.table, gate_now):
            if ships_before:
                logger.warning(
                    "sync_capture: строка %s стала local — на remote осталась история (Q-015)",
                    info.table,
                )
                if info.table == "document":
                    return _withdraw_op(session, info, now, before, project_id, lookup)
            return None
        promoted = not ships_before
    elif not _ships(info.table, gate_now):
        return None
    entity_uid = _entity_uid(info, now, lookup)
    if entity_uid is None:
        raise SyncCaptureError(f"{info.table}.uid: пуст")
    withheld = reg.withheld_columns(info.table, gate_now)
    if is_new or promoted:
        fields = _columns_payload(info, info.shipped - withheld, now, lookup)
        base: dict[str, object] = {}
    else:
        columns = changed - withheld
        if not columns:
            return None
        fields = _columns_payload(info, columns, now, lookup)
        base = _columns_payload(info, columns, before, lookup)
    op = _new_op(session, info, project_id, entity_uid, "upsert", fields, base, now)
    op.promoted = promoted
    return op


def document_shipped(session: Session | Connection, project_id: int, doc_uid: object) -> bool:
    """Документ уже бывал в changeset'е: своём отправленном или принятом (решение 20).

    Хвост не считается: неотправленную операцию никто, кроме реплики, не видел.
    """
    if not isinstance(doc_uid, str) or not doc_uid:
        return False
    tbl = cast("Table", SyncOpModel.__table__)
    hit = session.execute(
        select(tbl.c.op_id)
        .where(
            tbl.c.project_id == project_id,
            tbl.c.entity_kind == "document",
            tbl.c.entity_uid == doc_uid,
            tbl.c.changeset.is_not(None),
        )
        .limit(1)
    ).first()
    return hit is not None


def _withdraw_op(
    session: Session,
    info: TableInfo,
    now: Mapping[str, object],
    before: Mapping[str, object],
    project_id: object,
    lookup: _Lookup,
) -> PendingOp | None:
    """Операция отзыва уехавшего документа: ``upsert`` ровно с ``sensitivity``."""
    if not isinstance(project_id, int) or not document_shipped(
        lookup.conn, project_id, now.get(_UID)
    ):
        return None  # не уезжал — отзывать нечего
    entity_uid = _entity_uid(info, now, lookup)
    if entity_uid is None:
        raise SyncCaptureError(f"{info.table}.uid: пуст")
    column = reg.WITHDRAW_COLUMN
    op = _new_op(
        session,
        info,
        project_id,
        entity_uid,
        "upsert",
        {column: _serialize(info, column, now, lookup)},
        {column: _serialize(info, column, before, lookup)},
        now,
    )
    op.withdraw = True
    return op


def _delete_op(
    session: Session, info: TableInfo, state: InstanceState[object], lookup: _Lookup
) -> PendingOp | None:
    if info.identity is not None:
        return None  # журнал не удаляется операцией (решение 10)
    before = {c: v for c, v in _row_before(info, state).items() if v is not NO_VALUE}
    project_id = lookup.project_id(info.table, before)
    gate = gating_row(lookup, info.table, before, project_id)
    if not _ships(info.table, gate):
        return None
    entity_uid = _entity_uid(info, before, lookup)
    if entity_uid is None:
        raise SyncCaptureError(f"{info.table}.uid: пуст у удаляемой строки")
    withheld = reg.withheld_columns(info.table, gate)
    base = _columns_payload(info, info.shipped - withheld, before, lookup)
    return _new_op(session, info, project_id, entity_uid, "delete", {}, base, before)


# --- события --------------------------------------------------------------------


def _assign_uids(session: Session) -> None:
    """uid новой строки (и старой строки без uid) — до INSERT/UPDATE.

    Сначала обычные строки (uuid7), затем рёбра: их uid — uuid5 от uid
    концов, и конец, созданный тем же flush, к этому моменту уже с uid. Ребро,
    чей конец ещё не разрешим (FK проставит relationship), получит uid в
    ``after_flush``.
    """
    edges: list[tuple[TableInfo, object]] = []
    for obj in (*session.new, *session.dirty):
        info = table_info(attributes.instance_state(obj).mapper)
        if info is None or not info.has_uid or getattr(obj, _UID) is not None:
            continue
        if reg.spec_for(info.table).deterministic_uid is None:
            setattr(obj, _UID, str(uuid.uuid7()))
        else:
            edges.append((info, obj))
    if not edges:
        return
    lookup = _Lookup(session)
    with session.no_autoflush:
        for info, obj in edges:
            row = {column: getattr(obj, key) for column, key in info.columns}
            uid = _edge_uid(info, row, lookup)
            if uid is not None:
                setattr(obj, _UID, uid)


def _late_edge_uid(session: Session, info: TableInfo, obj: object, lookup: _Lookup) -> None:
    row = _row_now(info, obj)
    try:
        uid = reg.edge_uid(info.table, _edge_key(info, row, lookup))
    except _Unresolvable as exc:
        raise SyncCaptureError(f"{exc}") from None
    tbl = _table(attributes.instance_state(obj).mapper)
    lookup.conn.execute(update(tbl).where(tbl.c.row_id == row[_ROW_ID]).values(uid=uid))
    attributes.set_committed_value(obj, info.attr(_UID), uid)


def _fresh_row_ids(session: Session) -> None:
    """``row_id`` новой строки сущности с ревизиями — выше всех ``entity_id`` её вида.

    Адрес ревизии — ``(entity_kind, entity_id)``, а SQLite без AUTOINCREMENT
    отдаёт новой строке номер удалённой: её ревизии прирастали к новой строке
    и уезжали в дайджест под чужим uid — на этой реплике да, на остальных нет
    (CLO-027, сид 0). Строки, которые заводит pull, ``apply._fresh_row_id``
    нумерует так же. Только SQLite: последовательность Postgres номеров не
    переиспользует, а явный ``row_id`` разошёлся бы с ней.
    """
    if session.get_bind().dialect.name != "sqlite":
        return
    pending: dict[str, list[tuple[TableInfo, object]]] = {}
    for obj in session.new:
        info = table_info(attributes.instance_state(obj).mapper)
        if info is None or info.table not in _ENTITY_KIND_OF:
            continue
        if getattr(obj, info.attr(_ROW_ID), None) is None:
            pending.setdefault(info.table, []).append((info, obj))
    if not pending:
        return
    rev = Base.metadata.tables["revision"]
    with session.no_autoflush:
        for table, objs in pending.items():
            tbl = Base.metadata.tables[table]
            top_row = session.execute(select(func.max(tbl.c.row_id))).scalar() or 0
            top_rev = (
                session.execute(
                    select(func.max(rev.c.entity_id)).where(
                        rev.c.entity_kind == _ENTITY_KIND_OF[table]
                    )
                ).scalar()
                or 0
            )
            if top_rev <= top_row:
                continue  # номер удалённой строки ни одной ревизией не занят
            for i, (info, obj) in enumerate(objs, start=1):
                setattr(obj, info.attr(_ROW_ID), int(top_rev) + i)


#: Таблица → ``EntityKind`` её ревизий.
_ENTITY_KIND_OF: Final = MappingProxyType(
    {table: str(kind.value) for kind, table in reg.ENTITY_KIND_TABLES.items()}
)


def _before_flush(session: Session, _flush_context: object, _instances: object) -> None:
    _assign_uids(session)
    _fresh_row_ids(session)


def _after_flush(session: Session, flush_context: UOWTransaction) -> None:
    if _is_suspended(session):
        return
    lookup = _Lookup(session)
    upserts: list[tuple[int, PendingOp]] = []
    deletes: list[tuple[int, PendingOp]] = []
    order = _table_order()
    new = session.new
    for state, (isdelete, listonly) in list(flush_context.states.items()):
        info = table_info(state.mapper)
        if info is None or (listonly and not isdelete):
            continue
        if isdelete:
            op = _delete_op(session, info, state, lookup)
            if op is not None:
                deletes.append((order.get(info.table, 0), op))
            continue
        obj = state.obj()
        if obj is None:
            continue
        if info.has_uid and getattr(obj, _UID) is None:
            _late_edge_uid(session, info, obj, lookup)
        op = _upsert_op(session, info, state, is_new=obj in new, lookup=lookup)
        if op is not None:
            upserts.append((order.get(info.table, 0), op))
    if not upserts and not deletes:
        return
    captured = [op for _, op in sorted(upserts, key=lambda p: p[0])]
    captured += [op for _, op in sorted(deletes, key=lambda p: -p[0])]
    lowered = [op for op in captured if op.promoted and op.entity_kind == "document"]
    if lowered:
        from cod_doc.infra import sync_withdraw

        seen = {(op.entity_kind, op.entity_uid) for op in (*_pending(session), *captured)}
        for op in lowered:
            assert op.row is not None
            captured += sync_withdraw.reship_ops(session, op.row, op.project_id, seen)
    if _COMMITTING_KEY in session.info:
        # Flush commit'а после нашего before_commit (чужой before_commit
        # что-то поменял): хвост уже записан, пишем эти операции сразу —
        # иначе их выбросил бы конец транзакции.
        _write(session, lookup.conn, captured, session.info[_COMMITTING_KEY])
        return
    _pending(session).extend(captured)


def coalesce(ops: list[PendingOp]) -> list[PendingOp]:
    """Склеить подряд идущие upsert'ы одной сущности в транзакции.

    Сервис часто пишет строку в два flush'а (вставка, затем производное поле);
    на remote это одна правка. ``fields`` — последнее значение, ``base`` —
    значение до транзакции: ключ, впервые выставленный предыдущей операцией,
    в ``base`` не попадает (у новой строки его «до» не было). Удаление
    разрывает цепочку: delete + upsert того же ребра (замена) остаются двумя
    операциями.
    """
    out: list[PendingOp] = []
    last: dict[tuple[str, str], PendingOp] = {}
    for op in ops:
        key = (op.entity_kind, op.entity_uid)
        prev = last.get(key)
        if (
            op.kind == "upsert"
            and prev is not None
            and prev.kind == "upsert"
            and not (op.withdraw or prev.withdraw)
        ):
            for column, value in op.base.items():
                if column not in prev.fields and column not in prev.base:
                    prev.base[column] = value
            prev.fields.update(op.fields)
            prev.author = prev.author or op.author
            continue
        out.append(op)
        last[key] = op
    return out


_PROJECT_EXISTS = text("SELECT 1 FROM project WHERE row_id = :p")


def _replica_if_project_exists(conn: Connection, project_id: int) -> str | None:
    """Своя реплика проекта; ``None`` — проекта больше нет.

    Удаление проекта — не операция проекта: ``sync_op`` и ``replica`` висят на
    нём с ``ON DELETE CASCADE``, вставка упала бы на FK.
    """
    if conn.execute(_PROJECT_EXISTS, {"p": project_id}).first() is None:
        return None
    return self_replica(conn, project_id)


def _regate(lookup: _Lookup, pending: list[PendingOp]) -> list[PendingOp]:
    """Пересверить гейт реестра на состоянии к commit'у (Q-015, решение 16).

    Операция захвачена flush'ем, а документ мог стать restricted позже в той
    же транзакции: без пересверки уехали бы его тело, секции и ссылки на него.
    Строка — текущая из БД по uid (удалённая позже — снимок захвата), метки
    документов — текущие. Не проходит — операция выбрасывается; ссылка стала
    закрытой — её колонки вычищаются из ``fields`` и ``base``.
    """
    out: list[PendingOp] = []
    for op in pending:
        table = op.entity_kind
        spec = reg.spec_for(table)
        if op.withdraw:
            # Отзыв — операция над строкой, которая local по построению (решение 20).
            out.append(op)
            continue
        if op.row is None or (spec.row_is_local is None and table not in reg.DOC_REFS):
            out.append(op)
            continue
        row: Mapping[str, object] = op.row
        mapper = _mapper_for(table)
        if op.kind == "upsert" and mapper is not None and table in reg.UID_TABLES:
            tbl = _table(mapper)
            hit = lookup.conn.execute(select(tbl).where(tbl.c.uid == op.entity_uid)).mappings()
            current = hit.first()
            row = dict(current) if current is not None else row
        gate = gating_row(lookup, table, row, op.project_id)
        if not _ships(table, gate):
            continue
        for column in reg.withheld_columns(table, gate):
            op.fields.pop(column, None)
            op.base.pop(column, None)
        out.append(op)
    return out


_COMMITTING_KEY = "cod_doc.sync_capture.committing"


def _write(
    session: Session, conn: Connection, pending: list[PendingOp], author: str | None
) -> None:
    pending = _regate(_Lookup(session), pending)
    if not pending:
        return
    withdrawn = [op for op in pending if op.withdraw and op.row is not None]
    if withdrawn:
        from cod_doc.infra import sync_withdraw

        for op in withdrawn:
            assert op.row is not None
            # Хвост тела, захваченный до повышения, — до записи этой транзакции:
            # её собственные операции тела уже выбросил ``_regate``.
            sync_withdraw.purge_tail(session, op.project_id, op.row)
    if author is None and any(op.author is None for op in pending):
        logger.warning(
            "sync_capture: транзакция без автора — %d операций записаны как %s",
            len(pending),
            UNKNOWN_AUTHOR,
        )
    replicas: dict[int, str | None] = {}
    rows: list[dict[str, object]] = []
    for op in pending:
        if op.project_id not in replicas:
            replicas[op.project_id] = _replica_if_project_exists(conn, op.project_id)
        replica = replicas[op.project_id]
        if replica is None:
            continue  # проект удалён этой транзакцией: журнал ушёл бы каскадом
        rows.append(
            {
                "op_id": op.op_id,
                "project_id": op.project_id,
                "replica_uid": replica,
                "hlc": str(clock_for(conn, replica).now()),
                "entity_kind": op.entity_kind,
                "entity_uid": op.entity_uid,
                "kind": op.kind,
                "fields": _dumps(op.fields),
                "base": _dumps(op.base),
                "author": op.author or author or UNKNOWN_AUTHOR,
                "changeset": None,
            }
        )
    if rows:
        conn.execute(insert(cast("Table", SyncOpModel.__table__)), rows)


def _before_commit(session: Session) -> None:
    if session.in_nested_transaction():
        return  # RELEASE SAVEPOINT: внешняя транзакция ещё может откатиться
    session.flush()  # последний flush commit'а идёт после этого события
    pending = coalesce(session.info.pop(_PENDING_KEY, []))
    author = session.info.pop(AUTHOR_KEY, None)
    session.info[_COMMITTING_KEY] = author
    if pending:
        _write(session, session.connection(), pending, author)


def _within(tx: SessionTransaction | None, ancestor: SessionTransaction) -> bool:
    while tx is not None:
        if tx is ancestor:
            return True
        tx = tx.parent
    return False


def _after_soft_rollback(session: Session, previous: SessionTransaction) -> None:
    pending: list[PendingOp] | None = session.info.get(_PENDING_KEY)
    if pending:
        session.info[_PENDING_KEY] = [op for op in pending if not _within(op.tx, previous)]


def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None and not transaction.nested:
        # Корневая закончилась: commit уже записал хвост, rollback/close — выбросил.
        session.info.pop(_PENDING_KEY, None)
        session.info.pop(AUTHOR_KEY, None)
        session.info.pop(_COMMITTING_KEY, None)


_EVENTS = (
    ("before_flush", _before_flush),
    ("after_flush", _after_flush),
    ("before_commit", _before_commit),
    ("after_soft_rollback", _after_soft_rollback),
    ("after_transaction_end", _after_transaction_end),
)


# --- контракт §10.3 ---------------------------------------------------------------


_INSTALLED_MARK = "_cod_doc_sync_capture"


def install(session_factory: sessionmaker[Session]) -> None:
    """Повесить захват на фабрику; повторный вызов ничего не делает.

    Метка — атрибут класса сессии фабрики, а не ``event.contains``: реестр
    событий SQLAlchemy ключуется ``id(target)``, а фабрики живут по одной на
    вызов ``db_for_entry``. Класс собранной фабрики освобождается, ``id``
    достаётся новой, и ``contains`` отвечает «уже висит» про класс без
    слушателей (замер: 2970 из 3000 фабрик). Сессия такой фабрики писала бы
    мимо журнала — молча.
    """
    cls = session_factory.class_
    if cls.__dict__.get(_INSTALLED_MARK):
        return
    for name, fn in _EVENTS:
        event.listen(session_factory, name, fn)
    setattr(cls, _INSTALLED_MARK, True)


def set_author(session: Session, author: str) -> None:
    """Автор операций текущей транзакции (§10.3: ставят сервисы).

    Первый автор транзакции побеждает; системный (``system``, ``mcp:*``)
    уступает первому несистемному. Побочные записи сервиса идут от имени
    ``system`` (резолвер ссылок после ``patch_section``), а правки, не
    сброшенные flush'ем до них, захватываются только на commit: «последний
    автор» приписал бы правку человека системе.
    """
    current = session.info.get(AUTHOR_KEY)
    if current is None or (
        actor_kind_for_author(current) is ActorKind.SYSTEM
        and actor_kind_for_author(author) is not ActorKind.SYSTEM
    ):
        session.info[AUTHOR_KEY] = author


def record(
    session: Session,
    *,
    project_id: int,
    table: str,
    entity_uid: str,
    kind: OpKind,
    fields: Mapping[str, object],
    base: Mapping[str, object],
    author: str,
) -> str | None:
    """Явный захват для разрешённых bulk-мест (§3.4); та же транзакция.

    ``fields``/``base`` — уже сериализованные значения (FK как uid). Колонки,
    которые не уезжают, отбрасываются (fail-closed). Возвращает ``op_id``;
    ``None`` под ``suspended`` — применение чужих операций эха не пишет.
    ``project_id`` сверх сигнатуры §10.3: ``sync_op.project_id`` обязателен,
    а вывести его из uid без запроса нельзя.
    """
    if table not in CAPTURED_TABLES or table in reg.JOURNAL_IDS:
        # Журнал bulk-путей не имеет: ревизия пишется ORM и захватывается хуком.
        msg = f"sync_capture.record: {table!r} не захватывается"
        raise ValueError(msg)
    if kind not in _OP_KINDS:
        msg = f"sync_capture.record: kind {kind!r} не из {sorted(_OP_KINDS)}"
        raise ValueError(msg)
    if not entity_uid:
        msg = "sync_capture.record: пустой entity_uid"
        raise ValueError(msg)
    if _is_suspended(session):
        return None

    def keep(payload: Mapping[str, object]) -> dict[str, object]:
        return {
            c: v
            for c, v in payload.items()
            if c != _UID and reg.column_class(table, c) is reg.SyncClass.AUTHORED
        }

    op = PendingOp(
        op_id=str(ULID()),
        project_id=project_id,
        entity_kind=table,
        entity_uid=entity_uid,
        kind=kind,
        fields=keep(fields),
        base=keep(base),
        author=author,
        tx=_current_tx(session),
    )
    _pending(session).append(op)
    return op.op_id


def shipped_fields(
    session: Session,
    table: str,
    row: Mapping[str, object],
    columns: Iterable[str] | None = None,
) -> dict[str, object] | None:
    """Поля строки для ``record`` — ровно как их сериализует хук.

    ``row`` — строка целиком, со ссылками в виде ``row_id`` (как в БД). FK
    становятся uid конца, колонки, которые реестр придерживает для этой
    строки (``withheld_columns``), выброшены. ``columns`` — подмножество
    (``None`` — все уезжающие). ``None`` вместо словаря — строка не уезжает
    (``row_is_local``, решение 2 реестра): ``record`` для неё звать не нужно.
    Конец ссылки без uid — ``SyncCaptureError``, как в хуке.
    """
    mapper = _mapper_for(table)
    info = table_info(mapper) if mapper is not None else None
    if info is None:
        msg = f"sync_capture.shipped_fields: {table!r} не захватывается"
        raise ValueError(msg)
    lookup = _Lookup(session)
    project_id = lookup.project_id(table, row)
    gate = gating_row(lookup, table, row, project_id)
    if not _ships(table, gate):
        return None
    wanted = info.shipped if columns is None else info.shipped & frozenset(columns)
    return _columns_payload(info, wanted - reg.withheld_columns(table, gate), row, lookup)


def _shipped_line(
    lookup: _Lookup, info: TableInfo, row: Mapping[str, object], project_id: int
) -> tuple[str, str] | None:
    """``(uid сущности, поля JSON)`` строки проекта; ``None`` — строка не уезжает."""
    table = info.table
    if (
        _PROJECT_ID not in row
        and table != "project"
        and lookup.project_id(table, row) != project_id
    ):
        return None
    gate = gating_row(lookup, table, row, project_id)
    if not _ships(table, gate):
        return None
    entity_uid = _entity_uid(info, row, lookup)
    if entity_uid is None:
        raise SyncCaptureError(f"{table}.uid: пуст")
    withheld = reg.withheld_columns(table, gate) & info.shipped
    payload = _columns_payload(info, info.shipped - withheld, row, lookup)
    # Придержанная колонка входит в дайджест тем, что окажется у приёмника:
    # ``NULL`` или заглушка ``WITHHELD_FILL`` (CLO-027, F8). Приёмник не знает,
    # что колонку придержали, — он хеширует то, что лежит в строке; отправитель
    # хеширует то же самое вместо настоящего значения. Без этого ``sync verify``
    # у всех приёмников проекта с придержанной ссылкой показывал расхождение.
    for column in sorted(withheld):
        payload[column] = reg.WITHHELD_FILL.get((table, column))
    return entity_uid, _dumps(payload)


def shipped_state(session: Session, project_id: int) -> Iterator[tuple[str, str, str]]:
    """Уезжающее состояние проекта: ``(таблица, uid сущности, поля JSON)`` (RFC 29 §3.7).

    По строке на каждую захватываемую строку проекта, которую реестр
    выпускает: поля — ровно как их сериализует хук (``shipped_fields``: FK —
    uid конца, придержанные колонки выброшены), в той же JSON-форме, что
    ``sync_op.fields``. Строки ``row_is_local`` (restricted и всё под ним) не
    выдаются. Придержанная колонка (``withheld_columns``) выдаётся значением,
    которое окажется у приёмника: ``NULL`` или ``sync_registry.WITHHELD_FILL``.

    **Порядок — ``(таблица, uid)``**, ровно тот, что нужен дайджесту, и
    выдача потоковая: таблицы по имени, строки таблицы с колонкой ``uid`` —
    ``ORDER BY uid`` в SQL (на PostgreSQL с ``COLLATE "C"``, чтобы порядок
    совпал с байтовым у SQLite и Python). В памяти сортируются только таблицы
    без колонки ``uid`` — тег-таблицы с составным PK, чей uid вычисляется
    (``edge_uid``); они малы.

    Один ``_Lookup`` на весь обход: концы ссылок и метки документов читаются
    по разу, а не по разу на строку, как у ``shipped_fields``.
    """
    lookup = _Lookup(session)
    postgres = session.get_bind().dialect.name == "postgresql"
    for table in sorted(CAPTURED_TABLES):
        mapper = _mapper_for(table)
        info = table_info(mapper) if mapper is not None else None
        if mapper is None or info is None:
            # ``link_suggestion`` регистрируется лениво: пропуск дал бы разный
            # дайджест у процессов, импортировавших модель и нет.
            msg = f"sync_capture.shipped_state: модель {table!r} не загружена"
            raise LookupError(msg)
        tbl = _table(mapper)
        stmt = select(tbl)
        if table == "project":
            stmt = stmt.where(tbl.c.row_id == project_id)
        elif _PROJECT_ID in tbl.c:
            stmt = stmt.where(tbl.c.project_id == project_id)
        key = info.identity or (_UID if info.has_uid else None)
        if key is not None:
            stmt = stmt.order_by(tbl.c[key].collate("C") if postgres else tbl.c[key])
        rows = session.connection().execute(stmt).mappings()
        lines = (_shipped_line(lookup, info, dict(raw), project_id) for raw in rows)
        present = (line for line in lines if line is not None)
        for uid, fields in present if key is not None else sorted(present):
            yield table, uid, fields


@contextmanager
def suspended(session: Session) -> Iterator[None]:
    """Заглушить захват (применение чужих операций, §10.6).

    На выходе — flush под тем же флагом: при ``autoflush=False`` записи блока
    иначе ушли бы в БД уже после него и вернулись эхом.
    """
    session.info[_SUSPENDED_KEY] = session.info.get(_SUSPENDED_KEY, 0) + 1
    try:
        yield
        session.flush()
    finally:
        depth = session.info.get(_SUSPENDED_KEY, 1) - 1
        if depth:
            session.info[_SUSPENDED_KEY] = depth
        else:
            session.info.pop(_SUSPENDED_KEY, None)
