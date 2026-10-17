"""Удаление против правки: tombstone родителя и снимок поддерева (RFC 29 §3.7, Q-008; CLO-022).

**Удаление выигрывает.** Удаление родителя — одна операция ``delete`` по его uid;
поддерево на каждой реплике сносит тот же каскад (``ON DELETE CASCADE`` и
ORM-каскад), операций детей в журнале нет. Правка, которая попала под мёртвого
родителя, не применяется: она уходит в конфликт ``delete_edit`` со снимком
поддерева, и ``sync resolve --take theirs`` восстанавливает поддерево с этой
правкой (``conflicts.resolve_conflict``), ``--take ours`` оставляет удаление.

Конфликт — local (§3.2): он открывается на той реплике, которая сливала, и только
там есть данные для снимка. Случаев два, по тому, кто сливает.

1. **Удаление чужое, правка своя** (SCN-092: A удалил и запушил, B с правкой
   делает pull). Перед удалением ``apply`` проверяет каскад (restricted-строку
   он не снесёт — граница B2) и зовёт ``before_delete``: поддерево обходится
   по ``apply.cascade_edges`` от строки в БД; если свой хвост трогает хоть одну
   его строку, поддерево снимается со всеми правками хвоста (они уже в БД),
   операции хвоста над ним убираются — им некуда уехать, — и открывается
   конфликт. Без пересечения с хвостом удаление проходит молча.
2. **Удаление своё, правка чужая** (B запушил правку раньше, сливает A с
   удалением в хвосте). Строк поддерева в БД A уже нет, поэтому снимок
   собирается из журнала A: корень — ``base`` своей операции удаления (там все
   уезжающие поля), потомки — свёртка операций журнала тех сущностей, чей
   каскадный FK указывает на мёртвый корень. Чужая операция ложится в снимок
   поверх свёртки. Сущность, родившаяся до журнала, не свернётся целиком: её
   строка помечается ``partial``, и восстановить её resolve откажется.

Чужие операции, попавшие под одно мёртвое поддерево, копятся в одном конфликте
(один корень — один конфликт): в спецификации, которую ``pull`` ещё не записал,
или в открытой строке ``sync_conflict``.

**Форма конфликта.** ``entity_kind``/``entity_uid`` — корень, ``field`` пуст,
``theirs`` — снимок ``{"root": {table, uid}, "rows": [{table, uid, fields}]}``
(родитель раньше детей; ``uid`` пуст у тег-таблиц — их строка адресуется
концами), ``ours`` — ``{"delete_op", "edited": [[table, uid], …]}``. Поля — ровно
уезжающие колонки в форме changeset'а (``sync_capture.shipped_fields``): ни
``row_id``, ни local-колонок, ни restricted-строк в снимке нет.
"""

from __future__ import annotations

import dataclasses
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import delete, select

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.models import Base, SyncConflictModel, SyncOpModel
from cod_doc.services.sync_service import _local, apply
from cod_doc.services.sync_service._values import to_wire
from cod_doc.services.sync_service.changeset import FieldValue, canonical_json
from cod_doc.services.sync_service.merge import DELETE_EDIT, ConflictSpec

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service.changeset import Op
    from cod_doc.services.sync_service.merge import Context, EntityClash

__all__ = ["absorb_orphan", "before_delete", "resolve_entity", "rows_of"]

_UPSERT: Final = "upsert"
_DELETE: Final = "delete"
_OPEN: Final = "open"
_ROW_ID: Final = "row_id"
_PARTIAL: Final = "partial"
#: Сущность в ``ours.edited`` — пара (таблица, uid).
_PAIR: Final = 2
#: Порог ``IN (...)``: SQLite старше 3.32 держит 999 параметров.
_CHUNK: Final = 500

#: Сущность журнала: (таблица, uid); у тег-таблиц uid — uid ребра.
type Entity = tuple[str, str]


@dataclass(frozen=True, slots=True)
class _Root:
    table: str
    uid: str
    #: Своя операция удаления корня; ``None`` — конфликт по корню уже есть.
    delete_op: Op | None


# --- снимок -----------------------------------------------------------------------


def _children() -> dict[str, list[tuple[str, str]]]:
    by_parent: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for parent, child, column in apply.cascade_edges():
        by_parent[parent].append((child, column))
    return by_parent


def _entity_uid(table: str, row: Mapping[str, object], fields: Mapping[str, object]) -> str:
    if table in reg.UID_TABLES:
        return str(row.get("uid"))
    return reg.edge_uid(table, fields)


def _entry(table: str, uid: str, fields: Mapping[str, FieldValue]) -> dict[str, FieldValue]:
    return {
        "table": table,
        "uid": uid if table in reg.UID_TABLES else None,
        "fields": dict(fields),
    }


def _db_rows(session: Session, table: str, row_id: int) -> list[tuple[str, dict[str, object]]]:
    """Корень и всё, что снесёт его каскад, из БД — родитель раньше детей (обход в ширину)."""
    root = session.execute(
        select(Base.metadata.tables[table]).where(Base.metadata.tables[table].c.row_id == row_id)
    ).mappings()
    out = [(table, dict(row)) for row in root]
    by_parent = _children()
    seen: dict[str, set[int]] = defaultdict(set)
    seen[table].add(row_id)
    frontier: list[tuple[str, list[int]]] = [(table, [row_id])]
    while frontier:
        parent, ids = frontier.pop(0)
        for child, column in sorted(by_parent.get(parent, ())):
            tbl = Base.metadata.tables[child]
            found: list[int] = []
            for i in range(0, len(ids), _CHUNK):
                stmt = select(tbl).where(tbl.c[column].in_(ids[i : i + _CHUNK]))
                for raw in session.execute(stmt).mappings():
                    row = dict(raw)
                    child_id = row.get(_ROW_ID)
                    if isinstance(child_id, int):
                        if child_id in seen[child]:
                            continue
                        seen[child].add(child_id)
                        found.append(child_id)
                    out.append((child, row))
            if found:
                frontier.append((child, found))
    return out


def _snapshot_from_db(
    session: Session, table: str, row_id: int
) -> tuple[list[dict[str, FieldValue]], set[Entity]]:
    rows: list[dict[str, FieldValue]] = []
    entities: set[Entity] = set()
    for name, row in _db_rows(session, table, row_id):
        if name not in cap.CAPTURED_TABLES:
            continue
        shipped = cap.shipped_fields(session, name, row)
        if shipped is None:
            continue  # local-строка (owned-путь): в снимок не идёт, как и на remote
        fields = to_wire(name, shipped)
        uid = _entity_uid(name, row, fields)
        entities.add((name, uid))
        rows.append(_entry(name, uid, fields))
    return rows, entities


def _fold(ops: Iterable[Op]) -> tuple[dict[str, FieldValue], bool] | None:
    """Свёртка операций одной сущности: (поля, родилась ли в журнале); удалена — ``None``."""
    fields: dict[str, FieldValue] = {}
    born = False
    for i, op in enumerate(ops):
        if op.kind == _DELETE:
            return None
        if i == 0 and not op.base:
            born = True
        fields.update(op.fields)
    return fields, born


def _journal(session: Session, project_id: int, table: str) -> dict[str, list[Op]]:
    rows = session.scalars(
        select(SyncOpModel)
        .where(SyncOpModel.project_id == project_id, SyncOpModel.entity_kind == table)
        .order_by(SyncOpModel.hlc, SyncOpModel.op_id)
    )
    out: dict[str, list[Op]] = defaultdict(list)
    for row in rows:
        out[row.entity_uid].append(_local.op_of(row))
    return out


def _entity_fold(
    session: Session, project_id: int, entity: Entity
) -> tuple[dict[str, FieldValue], bool] | None:
    table, uid = entity
    rows = session.scalars(
        select(SyncOpModel)
        .where(
            SyncOpModel.project_id == project_id,
            SyncOpModel.entity_kind == table,
            SyncOpModel.entity_uid == uid,
        )
        .order_by(SyncOpModel.hlc, SyncOpModel.op_id)
    )
    ops = [_local.op_of(row) for row in rows]
    return _fold(ops) if ops else None


def _snapshot_from_journal(
    session: Session, ctx: Context, root: _Root
) -> list[dict[str, FieldValue]]:
    """Снимок поддерева, которого в БД уже нет: корень из ``base`` удаления, дети — журнал."""
    assert root.delete_op is not None
    rows = [_entry(root.table, root.uid, root.delete_op.base)]
    by_parent = _children()
    journals: dict[str, dict[str, list[Op]]] = {}
    seen: set[Entity] = {(root.table, root.uid)}
    frontier: list[Entity] = [(root.table, root.uid)]
    while frontier:
        parent, parent_uid = frontier.pop(0)
        for child, column in sorted(by_parent.get(parent, ())):
            if child not in cap.CAPTURED_TABLES:
                continue
            if child not in journals:
                journals[child] = _journal(session, ctx.project_id, child)
            for uid, ops in sorted(journals[child].items()):
                folded = _fold(ops)
                if folded is None or (child, uid) in seen:
                    continue
                fields, born = folded
                if fields.get(column) != parent_uid:
                    continue
                seen.add((child, uid))
                entry = _entry(child, uid, fields)
                if not born:
                    entry[_PARTIAL] = True
                rows.append(entry)
                frontier.append((child, uid))
    return rows


def rows_of(snapshot: object) -> list[dict[str, object]]:
    """Строки снимка из ``sync_conflict.theirs`` (уже разобранного JSON)."""
    if not isinstance(snapshot, dict):
        return []
    rows = snapshot.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _row_entity(row: Mapping[str, object]) -> Entity:
    table = str(row["table"])
    fields = cast("Mapping[str, object]", row["fields"])
    uid = row.get("uid")
    return table, str(uid) if uid is not None else reg.edge_uid(table, fields)


def _with_op(snapshot: dict[str, FieldValue], op: Op) -> dict[str, FieldValue]:
    """Снимок с чужой операцией поверх: правка — в строку, вставка — новой строкой."""
    rows = [dict(row) for row in rows_of(snapshot)]
    target = (op.entity_kind, op.entity_uid)
    for i, row in enumerate(rows):
        if _row_entity(row) != target:
            continue
        if op.kind == _DELETE:
            rows.pop(i)
        else:
            row["fields"] = {**cast("Mapping[str, object]", row["fields"]), **op.fields}
        return {**snapshot, "rows": cast("FieldValue", rows)}
    if op.kind == _UPSERT:
        entry: dict[str, object] = dict(_entry(op.entity_kind, op.entity_uid, op.fields))
        if op.base:
            entry[_PARTIAL] = True
        rows.append(entry)
    return {**snapshot, "rows": cast("FieldValue", rows)}


def _ours(delete_op: str | None, edited: Iterable[Entity]) -> dict[str, FieldValue]:
    return {
        "delete_op": delete_op,
        "edited": [[table, uid] for table, uid in sorted(set(edited))],
    }


def _edited(ours: object) -> set[Entity]:
    raw = ours.get("edited") if isinstance(ours, dict) else None
    if not isinstance(raw, list):
        return set()
    return {
        (str(item[0]), str(item[1]))
        for item in raw
        if isinstance(item, list) and len(item) == _PAIR
    }


# --- случай 1: чужое удаление ----------------------------------------------------


def before_delete(session: Session, ctx: Context, table: str, row_id: int, op: Op) -> None:
    """Чужое удаление строки ``row_id``: снимок и конфликт, если поддерево задевает свой хвост.

    Зовёт ``apply`` после проверки каскада и до ``session.delete``. Операции
    своего хвоста над поддеревом убираются: удаление выиграло, а правке
    несуществующей строки уехать некуда (другие реплики отвергли бы её как
    правку до журнала, SYNC-102). Правки при этом не теряются — они в снимке.
    """
    tail = _local.pending_ops(session, ctx.project_id)
    if not tail:
        return
    rows, entities = _snapshot_from_db(session, table, row_id)
    hits = [o for o in tail if (o.entity_kind, o.entity_uid) in entities]
    if not hits:
        return
    # Свои ревизии поддерева (journal, CLO-024) тоже не уезжают: ревизия удалённой
    # сущности local на любой реплике, а правка, которую она описывает, — в снимке.
    uids = {uid for _table, uid in entities}
    revisions = [
        o for o in tail if o.entity_kind in reg.JOURNAL_IDS and o.fields.get("entity_id") in uids
    ]
    dropped = [o.op_id for o in (*hits, *revisions)]
    session.execute(delete(SyncOpModel).where(SyncOpModel.op_id.in_(dropped)))
    session.flush()
    snapshot: dict[str, FieldValue] = {
        "root": {"table": table, "uid": op.entity_uid},
        "rows": cast("FieldValue", rows),
    }
    ctx.conflicts.append(
        ConflictSpec(
            entity_kind=table,
            entity_uid=op.entity_uid,
            field=None,
            kind=DELETE_EDIT,
            ours=_ours(op.op_id, ((o.entity_kind, o.entity_uid) for o in hits)),
            theirs=snapshot,
            base=None,
            theirs_op=op.op_id,
        )
    )


def resolve_entity(session: Session, ctx: Context, clash: EntityClash) -> bool:
    """Стратегия ``delete_edit`` (``merge.allow``): удаление выигрывает.

    Чужое удаление против своей правки или ссылки — ``True``: удаление
    применяется, снимок и конфликт пишет ``before_delete``. Чужая правка или
    вставка против своего удаления — в снимок конфликта, ``False``: операция не
    применяется.
    """
    if clash.theirs.kind == _DELETE:
        return True
    deletes = [o for o in clash.ours if o.kind == _DELETE]
    if not deletes:
        return True
    root_op = min(deletes, key=lambda o: (o.hlc, o.op_id))
    _absorb(session, ctx, _Root(root_op.entity_kind, root_op.entity_uid, root_op), clash.theirs)
    return False


# --- случай 2: своё удаление, чужая правка ----------------------------------------


def _open_conflicts(session: Session, project_id: int) -> list[SyncConflictModel]:
    return list(
        session.scalars(
            select(SyncConflictModel).where(
                SyncConflictModel.project_id == project_id,
                SyncConflictModel.kind == DELETE_EDIT,
                SyncConflictModel.status == _OPEN,
            )
        )
    )


def _dead(session: Session, ctx: Context) -> dict[Entity, _Root]:
    """Сущности в снимках открытых конфликтов (записанных и ещё нет) → корень."""
    snapshots: list[tuple[str, str, object]] = [
        (spec.entity_kind, spec.entity_uid, spec.theirs)
        for spec in ctx.conflicts
        if spec.kind == DELETE_EDIT
    ]
    snapshots += [
        (row.entity_kind, row.entity_uid, json.loads(row.theirs or "null"))
        for row in _open_conflicts(session, ctx.project_id)
    ]
    out: dict[Entity, _Root] = {}
    for table, uid, theirs in snapshots:
        for row in rows_of(theirs):
            out.setdefault(_row_entity(row), _Root(table, uid, None))
    return out


def _exists(session: Session, entity: Entity) -> bool:
    table, uid = entity
    if table not in reg.UID_TABLES:
        return False
    tbl = Base.metadata.tables[table]
    return session.execute(select(tbl.c.row_id).where(tbl.c.uid == uid)).first() is not None


def _root_of(
    session: Session,
    ctx: Context,
    deleted: Mapping[Entity, Op],
    dead: Mapping[Entity, _Root],
    entity: Entity,
) -> _Root | None:
    """Мёртвый корень, под которым лежит отсутствующая сущность: по свёртке журнала вверх."""
    edges = apply.cascade_edges()
    seen: set[Entity] = set()
    frontier = [entity]
    while frontier:
        current = frontier.pop(0)
        if current in seen:
            continue
        seen.add(current)
        if current in deleted:
            op = deleted[current]
            return _Root(op.entity_kind, op.entity_uid, op)
        if current in dead:
            return dead[current]
        folded = _entity_fold(session, ctx.project_id, current)
        if folded is None:
            continue
        fields, _born = folded
        for parent, child, column in sorted(edges):
            value = fields.get(column)
            if child != current[0] or not isinstance(value, str):
                continue
            if not _exists(session, (parent, value)):
                frontier.append((parent, value))
    return None


def _missing_targets(session: Session, op: Op) -> list[Entity]:
    out: list[Entity] = []
    if op.base and not _exists(session, (op.entity_kind, op.entity_uid)):
        out.append((op.entity_kind, op.entity_uid))
    info = apply._info(op.entity_kind)[1]
    for column, value in sorted(op.fields.items()):
        target = info.fk_targets.get(column)
        if target is None or not isinstance(value, str):
            continue
        if not _exists(session, (target, value)):
            out.append((target, value))
    return out


def absorb_orphan(session: Session, ctx: Context, op: Op) -> bool:
    """Операция над потомком мёртвого родителя (или со ссылкой на него) — в снимок.

    ``True`` — операция легла в конфликт и не применяется. ``False`` — мёртвого
    корня над ней нет, и ``apply`` поднимает свою ошибку как раньше (правка
    строки до журнала — CLO-029).
    """
    tail = _local.pending_ops(session, ctx.project_id)
    deleted = {(o.entity_kind, o.entity_uid): o for o in tail if o.kind == _DELETE}
    dead = _dead(session, ctx)
    if not deleted and not dead:
        return False
    for entity in _missing_targets(session, op):
        root = _root_of(session, ctx, deleted, dead, entity)
        if root is not None:
            _absorb(session, ctx, root, op)
            return True
    return False


def _absorb(session: Session, ctx: Context, root: _Root, op: Op) -> None:
    """Положить чужую операцию в снимок конфликта корня: в ещё не записанный, в открытый
    или в новый."""
    entity = (op.entity_kind, op.entity_uid)
    for i, spec in enumerate(ctx.conflicts):
        if spec.kind == DELETE_EDIT and spec.entity_uid == root.uid:
            snapshot = cast("dict[str, FieldValue]", spec.theirs)
            ctx.conflicts[i] = dataclasses.replace(
                spec,
                theirs=_with_op(snapshot, op),
                ours=_ours(_delete_op_of(spec.ours), {*_edited(spec.ours), entity}),
            )
            return
    for row in _open_conflicts(session, ctx.project_id):
        if row.entity_uid == root.uid:
            theirs = json.loads(row.theirs or "null")
            ours = json.loads(row.ours or "null")
            row.theirs = canonical_json(_with_op(theirs, op)).decode("utf-8")
            row.ours = canonical_json(_ours(_delete_op_of(ours), {*_edited(ours), entity})).decode(
                "utf-8"
            )
            session.flush()
            return
    if root.delete_op is None:  # снимок корня пропал между поиском и записью — баг
        msg = f"delete_edit: снимка корня {root.table} {root.uid} нет"
        raise AssertionError(msg)
    fresh: dict[str, FieldValue] = {
        "root": {"table": root.table, "uid": root.uid},
        "rows": cast("FieldValue", _snapshot_from_journal(session, ctx, root)),
    }
    ctx.conflicts.append(
        ConflictSpec(
            entity_kind=root.table,
            entity_uid=root.uid,
            field=None,
            kind=DELETE_EDIT,
            ours=_ours(root.delete_op.op_id, {entity}),
            theirs=_with_op(fresh, op),
            base=None,
            theirs_op=op.op_id,
        )
    )


def _delete_op_of(ours: object) -> str | None:
    value = ours.get("delete_op") if isinstance(ours, dict) else None
    return value if isinstance(value, str) else None
