"""Алиасы склейки клона — из проверенного журнала (RFC 29 §3.3, §3.6; CLO-052).

Алиас склейки (``entity_alias``, ``reason = merged``) дайджест головы не
покрывает, поэтому снапшот его не несёт: подложенный алиас увёл бы будущие
операции над выдуманным uid в живую строку (ревью CLO-029). Да и верного
алиаса у сборщика снапшота может не быть: алиас local, его пишет реплика,
которая склеивала, а её проигравший uid мог вообще не уехать (CLO-023).
Эталон для клона — обычная реплика, получившая ту же историю pull'ом:
у неё алиас есть ровно там, где обе вставки двойника лежат в истории.

**Вывод.** Клон проходит проверенный журнал (``snapshot.load_history``) в
порядке применения — changeset'ы по ``_local.topological``, операции внутри по
``(hlc, op_id)``, как их применяет pull — и повторяет решения ``apply`` о
ключах таблиц с ключом-именем (``KeyKind.NAME`` реестра), не трогая строк:

- вставка (``upsert`` с пустым ``base``) занимает свой натуральный ключ; FK в
  ключе — это строка, а не uid, поэтому секции документа-двойника после склейки
  документов сталкиваются по якорю так же, как в БД обычной реплики;
- ключ уже занят живой строкой другого uid — склейка: выживший по правилу
  ``keys.glue_survivor`` (меньший ``(hlc создания, uid)``, штамп создания —
  ``keys.created_stamp`` по журналу строки), проигравший — алиас на выжившего
  в этот момент. Цепочка (выживший потом сам проиграл) остаётся цепочкой, как
  у обычной реплики; ``apply.alias_target`` проходит её до конца;
- правка ключа — LWW по ``(hlc, op_id)`` поля; удаление освобождает ключ, и
  каскад (``apply.cascade_edges``) освобождает ключи детей.

Журнал перекладывается так же, как у обычной реплики: операции changeset'а
записываются под uid после одного шага по алиасам (``apply._record_ops``), а
склейка, где выжила вставка, переносит журнал проигравшего на выжившего
(``keys._rekey_tail``). Так LWW поздних правок выжившего видит штампы
проигравшего и на клоне.

**Реплика, догоняющая эпоху** (CLO-050, ``epoch_rebase.Rebaser``), идёт тем
же путём: ``snapshot.load`` выводит алиасы по всему журналу. Её собственные
алиасы склейки остаются как есть и служат выводу отправной точкой (журнал до
них уже переложен), дописываются только новые — из склеек в истории, которую
она не проигрывала.

**Граница вывода.** Строка, заведённая до журнала (её вставки нет ни в одном
changeset'е), ключ не занимает: обычная реплика без своих правок её тоже не
видит. Алиас, у которого проигравший оказался живой строкой загруженного
состояния, — признак расхождения вывода с историей: он не пишется (иначе
``_through_alias`` уводил бы операции живой строки), журнал этого uid
остаётся при нём, в лог — предупреждение.

Закрепление провизорного ID (``reason = pinned``, CLO-030) реплику не покидает:
его не несёт ни история, ни снапшот, и клон его не заводит — как и обычная
реплика.
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import and_, or_, select, update

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.models import Base, ChangesetModel, EntityAliasModel, SyncOpModel
from cod_doc.services.sync_service import _local, apply, keys

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service._local import ProjectRef

__all__ = ["Derived", "apply_derived", "derive", "name_keys"]

logger = logging.getLogger("cod_doc.services.sync_service.clone_aliases")

_UPSERT: Final = "upsert"
_DELETE: Final = "delete"
_EMPTY: Final = "{}"
_MERGED: Final = "merged"
_NO_STAMP: Final = ("", "")

#: Значение колонки в выводе: FK на таблицу с ключом-именем — номер строки
#: вывода (переживает смену uid при склейке), остальное — как в операции.
type Value = object
type Stamp = tuple[str, str]
type Key = tuple[Value, ...]


@functools.cache
def name_keys() -> Mapping[str, tuple[str, ...]]:
    """Таблица → колонки её ключа-имени (склейка, Q-007)."""
    out: dict[str, tuple[str, ...]] = {}
    for table in sorted(cap.CAPTURED_TABLES & reg.UID_TABLES):
        for key in reg.spec_for(table).natural_keys:
            if key.kind is reg.KeyKind.NAME:
                out[table] = key.columns
    return out


@functools.cache
def _cascades() -> Mapping[str, tuple[tuple[str, str], ...]]:
    """Родитель → (ребёнок с ключом-именем, его FK): удаление родителя освобождает ключ."""
    out: dict[str, list[tuple[str, str]]] = {}
    for parent, child, column in sorted(apply.cascade_edges()):
        if child in name_keys():
            out.setdefault(parent, []).append((child, column))
    return {parent: tuple(edges) for parent, edges in out.items()}


@functools.cache
def _fk_targets(table: str) -> Mapping[str, str]:
    info = cap.table_info(apply._mapper(table))
    return {} if info is None else dict(info.fk_targets)


@functools.cache
def _tracked(table: str) -> frozenset[str]:
    """Колонки, которые нужны выводу: ключ и FK каскада."""
    cascade = {
        column for edges in _cascades().values() for child, column in edges if child == table
    }
    return frozenset(name_keys()[table]) | frozenset(cascade)


@dataclass
class _Row:
    table: str
    uid: str
    values: dict[str, Value]
    stamps: dict[str, Stamp]
    alive: bool = True


@dataclass(frozen=True, slots=True)
class _Op:
    op_id: str
    hlc: str
    table: str
    uid: str
    kind: str
    fields: str
    base: str
    changeset: str

    @property
    def creation(self) -> bool:
        return self.kind == _UPSERT and self.base == _EMPTY


@dataclass(frozen=True, slots=True)
class Derived:
    """Итог вывода: алиасы в порядке появления и переписанные ``entity_uid`` журнала."""

    aliases: tuple[tuple[str, str, str], ...]  # новые: (таблица, проигравший, выживший)
    rewrites: Mapping[str, str]  # op_id → entity_uid
    raw: Mapping[str, tuple[str, str]]  # op_id → (таблица, uid из changeset'а)


@dataclass
class _Sim:
    rows: list[_Row] = field(default_factory=list)
    by_uid: dict[tuple[str, str], int] = field(default_factory=dict)
    occupied: dict[tuple[str, Key], int] = field(default_factory=dict)
    children: dict[tuple[str, str, Value], set[int]] = field(default_factory=dict)
    aliases: dict[tuple[str, str], str] = field(default_factory=dict)
    recorded: dict[str, tuple[str, str]] = field(default_factory=dict)
    by_recorded: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    creations: dict[str, str] = field(default_factory=dict)

    # --- значения и ключ ----------------------------------------------------------

    def _value(self, table: str, column: str, raw: object) -> Value:
        target = _fk_targets(table).get(column)
        if target is None or target not in name_keys() or not isinstance(raw, str):
            return raw
        found = self.rid(target, raw)
        return raw if found is None else found

    def _key(self, row: _Row) -> Key | None:
        values = tuple(row.values.get(c) for c in name_keys()[row.table])
        return None if any(v is None for v in values) else values

    def _index(self, rid: int, column: str, old: Value, new: Value) -> None:
        table = self.rows[rid].table
        if old is not None:
            self.children.get((table, column, old), set()).discard(rid)
        if new is not None:
            self.children.setdefault((table, column, new), set()).add(rid)

    def _set(self, rid: int, column: str, value: Value, stamp: Stamp) -> None:
        row = self.rows[rid]
        old = row.values.get(column)
        row.values[column] = value
        row.stamps[column] = stamp
        self._index(rid, column, old, value)

    def _occupy(self, rid: int) -> None:
        row = self.rows[rid]
        key = self._key(row)
        if key is not None:
            self.occupied.setdefault((row.table, key), rid)

    def _vacate(self, rid: int) -> None:
        row = self.rows[rid]
        key = self._key(row)
        if key is not None and self.occupied.get((row.table, key)) == rid:
            del self.occupied[(row.table, key)]

    # --- операции -----------------------------------------------------------------

    def rid(self, table: str, uid: str) -> int | None:
        """Строка вывода по uid; uid, которого вывод не видел, — через известные алиасы."""
        seen = {uid}
        while (found := self.by_uid.get((table, uid))) is None:
            uid = self.aliases.get((table, uid), uid)
            if uid in seen:
                return None
            seen.add(uid)
        return found

    def op(self, op: _Op) -> None:
        if op.table not in name_keys():
            if op.kind == _DELETE:  # родитель каскада без своего ключа (задача, план…)
                self._cascade(op.table, op.uid)
            return
        rid = self.rid(op.table, op.uid)
        live = rid is not None and self.rows[rid].alive
        if op.kind == _DELETE:
            if rid is not None and live:
                self._kill(rid)
            return
        fields: dict[str, object] = json.loads(op.fields)
        stamp = (op.hlc, op.op_id)
        if rid is not None and live:
            self._write(rid, fields, stamp)
        elif op.creation:
            self._create(op, fields, stamp)
        # Правка строки, которой нет (старше журнала или удалена): ключа не занимает.

    def _write(self, rid: int, fields: Mapping[str, object], stamp: Stamp) -> None:
        row = self.rows[rid]
        columns = [c for c in sorted(_tracked(row.table) & fields.keys())]
        wins = [c for c in columns if stamp > row.stamps.get(c, _NO_STAMP)]
        if not wins:
            return
        self._vacate(rid)
        for column in wins:
            self._set(rid, column, self._value(row.table, column, fields[column]), stamp)
        self._occupy(rid)

    def _create(self, op: _Op, fields: Mapping[str, object], stamp: Stamp) -> None:
        row = _Row(op.table, op.uid, {}, {})
        self.rows.append(row)
        rid = len(self.rows) - 1
        for column in sorted(_tracked(op.table) & fields.keys()):
            self._set(rid, column, self._value(op.table, column, fields[column]), stamp)
        key = self._key(row)
        holder = None if key is None else self.occupied.get((op.table, key))
        if holder is None or not self.rows[holder].alive:
            self.by_uid[(op.table, op.uid)] = rid
            self._occupy(rid)
            return
        row.alive = False  # вставка не легла: строка двойника — holder
        self._glue(holder, op, fields, stamp)

    def _glue(self, holder: int, op: _Op, fields: Mapping[str, object], stamp: Stamp) -> None:
        """``keys._glue`` без строк: кто выжил, алиас, журнал; поля — LWW против своих."""
        ours = self.rows[holder]
        ours_stamp = keys.created_stamp(self._creation_hlcs(op.table, ours.uid), ours.uid)
        if keys.glue_survivor((op.hlc, op.uid), ours_stamp) == op.uid:
            loser = ours.uid
            self._alias(op.table, loser, op.uid)
            self._rekey(op.table, loser, op.uid)
            ours.uid = op.uid
        else:
            self._alias(op.table, op.uid, ours.uid)
        self.by_uid[(op.table, op.uid)] = holder
        self._vacate(holder)
        for column in sorted(_tracked(op.table) & fields.keys()):
            value = self._value(op.table, column, fields[column])
            if value != ours.values.get(column) and stamp > ours.stamps.get(column, _NO_STAMP):
                self._set(holder, column, value, stamp)
        self._occupy(holder)

    def _kill(self, rid: int) -> None:
        self._vacate(rid)
        self.rows[rid].alive = False
        self._cascade(self.rows[rid].table, rid)

    def _cascade(self, parent: str, ident: Value) -> None:
        for child, column in _cascades().get(parent, ()):
            for rid in sorted(self.children.get((child, column, ident), ())):
                if self.rows[rid].alive:
                    self._kill(rid)

    # --- журнал -------------------------------------------------------------------

    def _alias(self, table: str, loser: str, survivor: str) -> None:
        self.aliases.setdefault((table, loser), survivor)

    def _creation_hlcs(self, table: str, uid: str) -> list[str]:
        return [
            self.creations[op_id]
            for op_id in self.by_recorded.get((table, uid), ())
            if op_id in self.creations
        ]

    def _rekey(self, table: str, loser: str, survivor: str) -> None:
        moved = self.by_recorded.pop((table, loser), set())
        for op_id in moved:
            self.recorded[op_id] = (table, survivor)
        self.by_recorded.setdefault((table, survivor), set()).update(moved)

    def record(self, ops: Iterable[_Op]) -> None:
        """Операции changeset'а — в журнал после его применения, через один шаг алиаса."""
        for op in ops:
            if op.table not in name_keys():
                continue
            target = (op.table, self.aliases.get((op.table, op.uid), op.uid))
            self.recorded[op.op_id] = target
            self.by_recorded.setdefault(target, set()).add(op.op_id)
            if op.creation:
                self.creations[op.op_id] = op.hlc


def _history(session: Session, project: ProjectRef) -> list[list[_Op]]:
    """Операции журнала, нужные выводу, — changeset'ами в порядке применения."""
    parents = {
        sha: tuple(json.loads(raw))
        for sha, raw in session.execute(
            select(ChangesetModel.sha, ChangesetModel.parents).where(
                ChangesetModel.project_id == project.row_id,
                ChangesetModel.applied_at.is_not(None),
            )
        )
    }
    tables = sorted(name_keys())
    parent_tables = sorted(_cascades())
    rows = session.execute(
        select(
            SyncOpModel.op_id,
            SyncOpModel.hlc,
            SyncOpModel.entity_kind,
            SyncOpModel.entity_uid,
            SyncOpModel.kind,
            SyncOpModel.fields,
            SyncOpModel.base,
            SyncOpModel.changeset,
        )
        .where(
            SyncOpModel.project_id == project.row_id,
            SyncOpModel.changeset.is_not(None),
            or_(
                SyncOpModel.entity_kind.in_(tables),
                and_(SyncOpModel.kind == _DELETE, SyncOpModel.entity_kind.in_(parent_tables)),
            ),
        )
        .order_by(SyncOpModel.hlc, SyncOpModel.op_id)
    )
    grouped: dict[str, list[_Op]] = {}
    for op_id, hlc, table, uid, kind, fields, base, changeset in rows:
        assert changeset is not None  # запрос берёт только упакованное
        op = _Op(op_id, hlc, table, uid, kind, fields, base, changeset)
        grouped.setdefault(changeset, []).append(op)
    return [grouped[sha] for sha in _local.topological(parents) if sha in grouped]


def derive(session: Session, project: ProjectRef) -> Derived:
    """Алиасы и журнал обычной реплики, проигравшей ту же историю (без записи)."""
    # Свои алиасы склейки (реплика, догоняющая эпоху, CLO-050): журнал до них уже
    # переложен, а операции истории, ссылающиеся на проигравших, идут через них.
    known = {
        (table, loser): uid
        for table, loser, uid in session.execute(
            select(
                EntityAliasModel.entity_kind, EntityAliasModel.alias, EntityAliasModel.uid
            ).where(
                EntityAliasModel.project_id == project.row_id,
                EntityAliasModel.reason == _MERGED,
            )
        )
    }
    sim = _Sim(aliases=dict(known))
    raw: dict[str, tuple[str, str]] = {}
    for ops in _history(session, project):
        for op in ops:
            sim.op(op)
        sim.record(ops)
        raw.update((op.op_id, (op.table, op.uid)) for op in ops if op.table in name_keys())
    rewrites = {op_id: uid for op_id, (_table, uid) in sim.recorded.items() if uid != raw[op_id][1]}
    aliases = tuple(
        (table, loser, uid)
        for (table, loser), uid in sim.aliases.items()
        if (table, loser) not in known
    )
    return Derived(aliases=aliases, rewrites=rewrites, raw=raw)


def _live(session: Session, table: str, uids: list[str]) -> set[str]:
    tbl = Base.metadata.tables[table]
    return set(session.scalars(select(tbl.c.uid).where(tbl.c.uid.in_(uids))))


def apply_derived(session: Session, project: ProjectRef, derived: Derived) -> int:
    """Записать вывод: алиасы ``merged`` и ``entity_uid`` журнала; вернуть число алиасов."""
    by_table: dict[str, list[str]] = {}
    for table, loser, _uid in derived.aliases:
        by_table.setdefault(table, []).append(loser)
    live = {(t, uid) for t, losers in by_table.items() for uid in _live(session, t, losers)}
    rewrites = dict(derived.rewrites)
    written = 0
    for table, loser, uid in derived.aliases:
        if (table, loser) in live:
            logger.warning(
                "clone: вывод склеил живую строку %s %s → %s — алиас не пишется", table, loser, uid
            )
            for op_id, origin in derived.raw.items():
                if origin == (table, loser):
                    rewrites.pop(op_id, None)
            continue
        session.add(
            EntityAliasModel(
                project_id=project.row_id, entity_kind=table, alias=loser, uid=uid, reason=_MERGED
            )
        )
        written += 1
    if rewrites:
        session.execute(
            update(SyncOpModel),
            [{"op_id": op_id, "entity_uid": uid} for op_id, uid in sorted(rewrites.items())],
        )
    session.flush()
    return written
