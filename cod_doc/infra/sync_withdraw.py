"""Отзыв документа, ставшего restricted, и его возврат после понижения (CLO-046).

Сосед ``sync_capture``: пользуется его чтением строк и сериализацией, а
захват зовёт его в двух местах — понижение документа (``reship_ops``) и запись
операции отзыва (``purge_tail``). Применение чужого отзыва зовёт
``withdraw_locally``. Решение и его границы — решение 20 ``sync_registry``.

**Хвост** (``purge_tail``). Операции тела документа, захваченные до
повышения и ещё не отправленные, уехали бы следующим push'ем: push хвост не
пересверяет. Отзыв удаляет из хвоста своей реплики операции документа, его
тела (``DOCUMENT_BODY``: по uid строки или по ``document_id`` в полях — так
находится и удалённая строка), ревизии этих сущностей и операции строк,
которые после повышения не проходят гейт; у прошедших вычищаются
придержанные колонки. Хвост никто, кроме реплики, не видел — удалить его
можно без следа на remote.

**Понижение** (``reship_ops``) — первый upsert (``base`` пуст) тела, ссылок
на документ и ревизий, которых remote не видел. ``base`` пуст нарочно:
получатель держит строки тела с затёртым текстом, и чужая операция создания
поверх существующей строки идёт LWW по полю (секция C, находка 7), а не 3-way
merge против затёртого. У ссылок (``scenario``, ``plan``…) уезжают только
освобождённые колонки: остальные уезжали всё время, и их новая отправка
затёрла бы ещё не принятые чужие правки. Тело, удалённое владельцем, пока
документ был restricted, уходит удалением: строку, которую реплика видела в
журнале как тело документа и которой у неё больше нет.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import and_, delete, or_, select, update
from ulid import ULID

from cod_doc.domain.entities import EntityKind, Sensitivity
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.hlc import self_replica
from cod_doc.infra.models import Base, RevisionModel, SyncOpModel

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy import Table
    from sqlalchemy.orm import Session
    from sqlalchemy.sql.elements import ColumnElement

_UID: Final = "uid"
_ROW_ID: Final = "row_id"
_DOCUMENT: Final = "document"
_SECTION: Final = "section"
_REVISION: Final = "revision"
_DELETE: Final = "delete"
_CHUNK: Final = 500

Row = dict[str, object]


def _ops_table() -> Table:
    return cast("Table", SyncOpModel.__table__)


def _info(table: str) -> cap.TableInfo:
    mapper = cap._mapper_for(table)
    info = cap.table_info(mapper) if mapper is not None else None
    if info is None:
        msg = f"sync_withdraw: {table!r} не захватывается"
        raise LookupError(msg)
    return info


def _loads(raw: object) -> dict[str, object]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return dict(value) if isinstance(value, dict) else {}


def _rows(session: Session, table: str, where: ColumnElement[bool]) -> list[Row]:
    tbl = Base.metadata.tables[table]
    return [dict(r) for r in session.connection().execute(select(tbl).where(where)).mappings()]


def _body_rows(session: Session, doc_row_id: object) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for table, column in reg.DOCUMENT_BODY.items():
        tbl = Base.metadata.tables[table]
        out[table] = _rows(session, table, tbl.c[column] == doc_row_id)
    return out


# --- хвост ---------------------------------------------------------------------------


def purge_tail(session: Session, project_id: int, doc: Mapping[str, object]) -> list[Row]:
    """Убрать из хвоста своей реплики операции отозванного документа (решение 20).

    ``doc`` — строка документа (``row_id``, ``uid``). Возвращает удалённые
    операции (``entity_kind``, ``entity_uid``, ``kind``, ``fields``, ``base``,
    ``hlc``) — получатель кладёт их в карантин. Зовут захват (владелец, перед
    записью отзыва) и применение чужого отзыва (получатель).

    Тело документа — неподвижная точка: живые строки тела плюс каждая
    сущность, которую хоть одна операция хвоста (``fields`` или ``base``,
    любого вида) связывает с документом через ``DOCUMENT_BODY``. Один проход
    по порядку HLC пропустил бы правку секции, стоящую раньше операции,
    называющей документ (удаление с ``document_id`` в ``base``).
    """
    doc_uid = doc.get(_UID)
    if not isinstance(doc_uid, str):
        return []
    conn = session.connection()
    ops = _ops_table()
    tail = conn.execute(
        select(
            ops.c.op_id,
            ops.c.hlc,
            ops.c.entity_kind,
            ops.c.entity_uid,
            ops.c.kind,
            ops.c.fields,
            ops.c.base,
        )
        .where(
            ops.c.project_id == project_id,
            ops.c.changeset.is_(None),
            ops.c.replica_uid == self_replica(conn, project_id),
        )
        .order_by(ops.c.hlc, ops.c.op_id)
    ).all()
    if not tail:
        return []
    subtree = _subtree(session, doc, tail)
    drop: set[str] = set()
    for op in tail:
        if (op.entity_kind in (_DOCUMENT, *reg.DOCUMENT_BODY) and op.entity_uid in subtree) or (
            op.entity_kind == _REVISION and _loads(op.fields).get("entity_id") in subtree
        ):
            drop.add(op.op_id)
    drop |= _regate_tail(session, project_id, [op for op in tail if op.op_id not in drop])
    ids = sorted(drop)
    for i in range(0, len(ids), _CHUNK):
        conn.execute(delete(ops).where(ops.c.op_id.in_(ids[i : i + _CHUNK])))
    return [
        {
            "hlc": op.hlc,
            "entity_kind": op.entity_kind,
            "entity_uid": op.entity_uid,
            "kind": op.kind,
            "fields": _loads(op.fields),
            "base": _loads(op.base),
        }
        for op in tail
        if op.op_id in drop
    ]


def _subtree(session: Session, doc: Mapping[str, object], tail: Iterable[Any]) -> set[str]:
    """uid документа и его тела — неподвижная точка по живым строкам и хвосту."""
    doc_uid = str(doc.get(_UID))
    subtree = {doc_uid}
    for rows in _body_rows(session, doc.get(_ROW_ID)).values():
        subtree.update(str(r[_UID]) for r in rows if isinstance(r.get(_UID), str))
    links: dict[str, set[object]] = {}
    for op in tail:
        column = reg.DOCUMENT_BODY.get(op.entity_kind)
        if column is None:
            continue
        owners = links.setdefault(op.entity_uid, set())
        owners.update((_loads(op.fields).get(column), _loads(op.base).get(column)))
    changed = True
    while changed:
        changed = False
        for uid, owners in links.items():
            if uid not in subtree and owners & subtree:
                subtree.add(uid)
                changed = True
    return subtree


def _regate_tail(session: Session, project_id: int, tail: Iterable[object]) -> set[str]:
    """Upsert'ы хвоста по строкам со ссылкой на документ — гейт по текущей строке.

    Не проходит — операция удаляется (возвращается её ``op_id``); придержанные
    колонки вычищаются из ``fields``/``base`` на месте. Как ``_regate`` захвата,
    только по уже записанному хвосту.
    """
    lookup = cap._Lookup(session)
    ops = _ops_table()
    drop: set[str] = set()
    for op in tail:
        table = str(getattr(op, "entity_kind"))  # noqa: B009
        if (
            getattr(op, "kind") == _DELETE  # noqa: B009
            or table not in reg.DOC_REFS
            or table not in reg.UID_TABLES
            or table in reg.DOCUMENT_BODY
        ):
            continue
        tbl = Base.metadata.tables[table]
        found = _rows(session, table, tbl.c.uid == getattr(op, "entity_uid"))  # noqa: B009
        if not found:
            continue
        gate = cap.gating_row(lookup, table, found[0], project_id)
        if not cap._ships(table, gate):
            drop.add(str(getattr(op, "op_id")))  # noqa: B009
            continue
        withheld = reg.withheld_columns(table, gate)
        fields, base = _loads(getattr(op, "fields")), _loads(getattr(op, "base"))  # noqa: B009
        if withheld & (fields.keys() | base.keys()):
            for column in withheld:
                fields.pop(column, None)
                base.pop(column, None)
            session.connection().execute(
                update(ops)
                .where(ops.c.op_id == getattr(op, "op_id"))  # noqa: B009
                .values(fields=cap._dumps(fields), base=cap._dumps(base))
            )
    return drop


# --- получатель ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """Итог применения чужого отзыва у получателя."""

    #: ``row_id`` оставшихся секций — по ним пересобирается derived.
    sections: list[int]
    #: Карантин своих неотправленных правок; ``None`` — их не было.
    quarantine: dict[str, object] | None


def withdraw_locally(session: Session, project_id: int, doc: Mapping[str, object]) -> Withdrawn:
    """Чужой отзыв применён: вычистить свой хвост, затереть текст тела.

    Строки тела остаются (решение 20 реестра): после отзыва они local и в
    дайджест не входят ни здесь, ни у владельца. Исключение — строки, которых
    журнал не знает вовсе (их создание было в вычищенном хвосте): никто, кроме
    реплики, их не видел, и после понижения они остались бы только здесь.
    Они удаляются — как и ревизии документа и тела, которых журнал не знает:
    иначе после понижения сущность снова уезжала бы, и такая ревизия вошла бы
    в дайджест только этой реплики. Запись — под ``suspended`` вызывающего,
    операций не даёт.

    Своя работа не пропадает молча: если хвост держал правки документа, они
    и тексты затрагиваемых строк до затирания возвращаются в ``quarantine`` —
    вызывающий пишет их local-записью (событие ``sync.withdraw_quarantined``).
    """
    dropped = purge_tail(session, project_id, doc)
    _drop_unshipped_revisions(session, project_id, doc)
    lookup = cap._Lookup(session)
    sections: list[int] = []
    edited = {str(op["entity_uid"]) for op in dropped}
    texts: list[dict[str, object]] = []
    for table, column in reg.DOCUMENT_BODY.items():
        blanks = {c: v for (t, c), v in reg.WITHDRAW_BLANK.items() if t == table}
        mapper = cap._mapper_for(table)
        assert mapper is not None
        info = _info(table)
        model = mapper.class_
        stmt = select(model).where(getattr(model, info.attr(column)) == doc.get(_ROW_ID))
        objs = list(session.scalars(stmt))
        rows = {id(obj): {c: getattr(obj, a) for c, a in info.columns} for obj in objs}
        uids = {key: cap._entity_uid(info, row, lookup) for key, row in rows.items()}
        known = _known_uids(session, project_id, table, [u for u in uids.values() if u])
        for obj in objs:
            uid = uids[id(obj)]
            if uid in edited or uid not in known:
                texts.append(
                    {"table": table, "uid": uid}
                    | {c: rows[id(obj)][c] for c in _QUARANTINE_COLUMNS.get(table, ())}
                )
            if uid not in known:
                session.delete(obj)
                continue
            for blank_column, value in blanks.items():
                setattr(obj, info.attr(blank_column), value)
            if table == _SECTION:
                sections.append(int(getattr(obj, info.attr(_ROW_ID))))
        session.flush()
    quarantine: dict[str, object] | None = None
    if dropped or texts:
        quarantine = {
            "doc_key": doc.get("doc_key"),
            "doc_uid": doc.get(_UID),
            "ops": dropped,
            "rows": texts,
        }
    return Withdrawn(sections, quarantine)


#: Что из строки тела сохраняет карантин: текст и то, по чему его найти.
_QUARANTINE_COLUMNS: Final[Mapping[str, tuple[str, ...]]] = {
    _SECTION: ("anchor", "heading", "body"),
    "doc_comment": ("anchor", "quote", "body", "author"),
}


def _drop_unshipped_revisions(session: Session, project_id: int, doc: Mapping[str, object]) -> None:
    """Удалить ревизии документа и тела, которых нет в журнале операций реплики.

    Их операции вычистил ``purge_tail`` (или их не было: сущность была local).
    Цепочку не рвём: ребёнок удалённой ревизии получает её родителя.
    """
    affected = [(_DOCUMENT, dict(doc))]
    for table, rows in _body_rows(session, doc.get(_ROW_ID)).items():
        affected += [(table, row) for row in rows]
    model = RevisionModel
    revisions: list[RevisionModel] = []
    for table, row in affected:
        kind = cap._ENTITY_KIND_OF.get(table)
        if kind is not None:
            revisions += session.scalars(
                select(model).where(model.entity_kind == kind, model.entity_id == row[_ROW_ID])
            )
    known = _known_uids(session, project_id, _REVISION, [r.revision_id for r in revisions])
    for rev in revisions:
        if rev.revision_id in known:
            continue
        for child in session.scalars(
            select(model).where(model.parent_revision_id == rev.revision_id)
        ):
            child.parent_revision_id = rev.parent_revision_id
        session.delete(rev)
        session.flush()


def _known_uids(session: Session, project_id: int, table: str, uids: list[str]) -> set[str]:
    """uid, по которым в журнале реплики есть хоть одна операция."""
    ops = _ops_table()
    known: set[str] = set()
    for i in range(0, len(uids), _CHUNK):
        known.update(
            session.connection()
            .execute(
                select(ops.c.entity_uid).where(
                    ops.c.project_id == project_id,
                    ops.c.entity_kind == table,
                    ops.c.entity_uid.in_(uids[i : i + _CHUNK]),
                )
            )
            .scalars()
        )
    return known


# --- понижение -------------------------------------------------------------------------


def reship_ops(
    session: Session,
    doc: Mapping[str, object],
    project_id: int,
    seen: set[tuple[str, str]],
) -> list[cap.PendingOp]:
    """Первый upsert всего, что держал restricted-документ (решение 20 реестра).

    ``doc`` — строка документа после понижения; ``seen`` — ``(таблица, uid)``
    операций, уже захваченных транзакцией (их не дублируем), пополняется.
    Порядок: тело, удаления тела, ссылки, ревизии — ревизия применяется на
    получателе, только когда её сущность у него уже уезжает.
    """
    lookup = cap._Lookup(session)
    out: list[cap.PendingOp] = []
    affected: list[tuple[str, Row]] = [(_DOCUMENT, dict(doc))]
    body = _body_rows(session, doc.get(_ROW_ID))
    for table, rows in body.items():
        info = _info(table)
        present: set[str] = set()
        for row in rows:
            op = _first_upsert(session, lookup, info, row, project_id, info.shipped)
            if op is None:
                continue
            present.add(op.entity_uid)
            affected.append((table, row))
            if (table, op.entity_uid) not in seen:
                out.append(op)
        out += _gone_body(session, info, project_id, doc.get(_UID), present)
    revisions = _revision_ids(session, affected)
    for table, row, columns in _referrers(session, lookup, project_id, doc, body, revisions):
        op = _first_upsert(session, lookup, _info(table), row, project_id, columns)
        if op is not None:
            affected.append((table, row))
            out.append(op)
    out += _unshipped_revisions(session, lookup, project_id, affected, seen)
    seen.update((op.entity_kind, op.entity_uid) for op in out)
    return out


def _first_upsert(
    session: Session,
    lookup: cap._Lookup,
    info: cap.TableInfo,
    row: Mapping[str, object],
    project_id: int,
    columns: frozenset[str],
) -> cap.PendingOp | None:
    gate = cap.gating_row(lookup, info.table, row, project_id)
    if not cap._ships(info.table, gate):
        return None
    wanted = (columns & info.shipped) - reg.withheld_columns(info.table, gate)
    entity_uid = cap._entity_uid(info, row, lookup)
    if not wanted or entity_uid is None:
        return None
    fields = cap._columns_payload(info, wanted, row, lookup)
    op = cap._new_op(session, info, project_id, entity_uid, "upsert", fields, {}, row)
    op.promoted = True
    return op


def _gone_body(
    session: Session,
    info: cap.TableInfo,
    project_id: int,
    doc_uid: object,
    present: set[str],
) -> list[cap.PendingOp]:
    """Удаления тела, которое журнал знает за документом, а реплика уже снесла."""
    ops = _ops_table()
    column = reg.DOCUMENT_BODY[info.table]
    last: dict[str, tuple[str, object, dict[str, object]]] = {}
    for op in session.connection().execute(
        select(ops.c.entity_uid, ops.c.kind, ops.c.fields)
        .where(ops.c.project_id == project_id, ops.c.entity_kind == info.table)
        .order_by(ops.c.hlc, ops.c.op_id)
    ):
        fields = _loads(op.fields)
        _, owner, known = last.get(op.entity_uid, ("", None, {}))
        last[op.entity_uid] = (op.kind, fields.get(column, owner), {**known, **fields})
    key = reg.spec_for(info.table).deterministic_uid or ()
    out: list[cap.PendingOp] = []
    for uid, (kind, owner, known) in sorted(last.items()):
        if kind == _DELETE or owner != doc_uid or uid in present:
            continue
        # Ребро ищется по колонкам ключа (uid у тег-таблиц нет): они и есть base.
        base = {c: known[c] for c in key if c in known}
        out.append(
            cap.PendingOp(
                op_id=str(ULID()),
                project_id=project_id,
                entity_kind=info.table,
                entity_uid=uid,
                kind=_DELETE,
                fields={},
                base=base,
                author=session.info.get(cap.AUTHOR_KEY),
                tx=cap._current_tx(session),
            )
        )
    return out


def _revision_ids(session: Session, affected: Iterable[tuple[str, Row]]) -> list[int]:
    """``row_id`` ревизий документа и его тела — цели ``approval_doc_revision_link``."""
    out: list[int] = []
    for table, row in affected:
        kind = cap._ENTITY_KIND_OF.get(table)
        if kind is None:
            continue
        tbl = Base.metadata.tables[_REVISION]
        stmt = select(tbl.c.row_id).where(
            tbl.c.entity_kind == kind, tbl.c.entity_id == row.get(_ROW_ID)
        )
        out += [int(v) for v in session.connection().execute(stmt).scalars()]
    return out


def _ref_condition(
    tbl: Table,
    column: str,
    target: str,
    doc: Mapping[str, object],
    sections: list[object],
    revisions: list[int],
) -> ColumnElement[bool] | None:
    col = tbl.c[column]
    doc_key = str(doc.get("doc_key"))
    if target == _DOCUMENT:
        return col == doc.get(_ROW_ID)
    if target == _SECTION:
        return col.in_(sections) if sections else None
    if target == "doc_key":
        return col == doc_key
    if target == "to_ref":
        return or_(col == doc_key, col.startswith(f"{doc_key}#", autoescape=True))
    if target == "entity_kind":
        kind = tbl.c["entity_kind"]
        on_doc = and_(kind == EntityKind.DOCUMENT.value, col == doc.get(_ROW_ID))
        if not sections:
            return on_doc
        return or_(on_doc, and_(kind == EntityKind.SECTION.value, col.in_(sections)))
    if target == _REVISION:
        return col.in_(revisions) if revisions else None
    msg = f"sync_withdraw: неизвестная цель ссылки {target!r}"
    raise AssertionError(msg)


def _referrers(
    session: Session,
    lookup: cap._Lookup,
    project_id: int,
    doc: Mapping[str, object],
    body: Mapping[str, list[Row]],
    revisions: list[int],
) -> list[tuple[str, Row, frozenset[str]]]:
    """Строки вне тела со ссылкой на документ или его секции и колонки для отправки.

    Ссылка целиком (``whole_row``) — строка уезжает вся; иначе — колонка ссылки
    и то, что реестр придерживал вместе с ней.
    """
    sections = [r[_ROW_ID] for r in body.get(_SECTION, ())]
    found: dict[tuple[str, object], tuple[Row, set[str]]] = {}
    for (table, column), target in cap.DOC_REF_TARGETS.items():
        if table in reg.DOCUMENT_BODY or table in reg.JOURNAL_IDS:
            continue
        tbl = Base.metadata.tables[table]
        where = _ref_condition(tbl, column, target, doc, sections, revisions)
        if where is None:
            continue
        ref = next(r for r in reg.DOC_REFS[table] if r.column == column)
        columns = _info(table).shipped if ref.whole_row else {column, *ref.withheld}
        for row in _rows(session, table, where):
            if lookup.project_id(table, row) != project_id:
                continue
            _, acc = found.setdefault((table, row[_ROW_ID]), (row, set()))
            acc.update(columns)
    return [(t, row, frozenset(cols)) for (t, _), (row, cols) in sorted(found.items(), key=repr)]


def _unshipped_revisions(
    session: Session,
    lookup: cap._Lookup,
    project_id: int,
    affected: Iterable[tuple[str, Row]],
    seen: set[tuple[str, str]],
) -> list[cap.PendingOp]:
    """Ревизии затронутых сущностей, которых нет в журнале операций реплики."""
    info = _info(_REVISION)
    tbl = Base.metadata.tables[_REVISION]
    rows: list[Row] = []
    for table, row in affected:
        kind = cap._ENTITY_KIND_OF.get(table)
        if kind is not None:
            rows += _rows(
                session, _REVISION, and_(tbl.c.entity_kind == kind, tbl.c.entity_id == row[_ROW_ID])
            )
    known = _known_uids(
        session, project_id, _REVISION, sorted({str(r["revision_id"]) for r in rows})
    )
    out: list[cap.PendingOp] = []
    for row in sorted(rows, key=lambda r: int(cast("int", r[_ROW_ID]))):
        uid = str(row["revision_id"])
        if uid in known or (_REVISION, uid) in seen:
            continue
        gate = cap.gating_row(lookup, _REVISION, row, project_id)
        if not cap._ships(_REVISION, gate):
            continue
        fields = cap._columns_payload(info, info.shipped, row, lookup)
        out.append(cap._new_op(session, info, project_id, uid, "upsert", fields, {}, row))
        known.add(uid)
    return out


# --- чужие операции над отозванным ---------------------------------------------------


def _doc_by_key(lookup: cap._Lookup, project_id: int, doc_key: object) -> Row | None:
    tbl = Base.metadata.tables[_DOCUMENT]
    hit = (
        lookup.conn.execute(
            select(tbl).where(tbl.c.project_id == project_id, tbl.c.doc_key == doc_key)
        )
        .mappings()
        .first()
    )
    return dict(hit) if hit is not None else None


def _entity_document(lookup: cap._Lookup, kind: object, row_id: object) -> Row | None:
    if kind == EntityKind.SECTION.value:
        section = lookup.row(_SECTION, row_id)
        row_id = section.get("document_id") if section is not None else None
    elif kind != EntityKind.DOCUMENT.value:
        return None
    found = lookup.row(_DOCUMENT, row_id) if row_id is not None else None
    return dict(found) if found is not None else None


def _ref_document(
    lookup: cap._Lookup, project_id: int, table: str, column: str, row: Mapping[str, object]
) -> Row | None:
    """Документ, на который смотрит ссылка ``column`` строки; ``None`` — не нашёлся."""
    value = row.get(column)
    if value is None:
        return None
    target = cap.DOC_REF_TARGETS[(table, column)]
    if target == _DOCUMENT:
        return _entity_document(lookup, EntityKind.DOCUMENT.value, value)
    if target == _SECTION:
        return _entity_document(lookup, EntityKind.SECTION.value, value)
    if target == "doc_key":
        return _doc_by_key(lookup, project_id, value)
    if target == "to_ref":
        return _doc_by_key(lookup, project_id, str(value).partition("#")[0])
    if target == "entity_kind":
        return _entity_document(lookup, row.get("entity_kind"), value)
    tbl = Base.metadata.tables[_REVISION]
    hit = lookup.conn.execute(
        select(tbl.c.entity_kind, tbl.c.entity_id).where(tbl.c.revision_id == value)
    ).first()
    return _entity_document(lookup, hit[0], hit[1]) if hit is not None else None


def _relaxed(
    session: Session, project_id: int, table: str, row: Mapping[str, object]
) -> tuple[Row, Row] | None:
    """Гейт-строка и она же с метками отозванных документов, заменёнными на уезжающую.

    ``None`` — отозванного документа на пути строки нет. Отозванный —
    restricted здесь и бывал в changeset'е; свой никогда не уезжавший
    restricted-документ не подменяется.
    """
    if table == _DOCUMENT:
        if reg.is_cleared(row.get(reg.WITHDRAW_COLUMN)) or not cap.document_shipped(
            session, project_id, row.get(_UID)
        ):
            return None
        return dict(row), {**row, reg.WITHDRAW_COLUMN: Sensitivity.INTERNAL.value}
    refs = reg.DOC_REFS.get(table)
    if not refs or table not in cap.CAPTURED_TABLES:
        return None
    lookup = cap._Lookup(session)
    gate = cap.gating_row(lookup, table, row, project_id)
    relaxed = dict(gate)
    for ref in refs:
        key = reg.sensitivity_key(ref.column)
        if key not in gate or reg.is_cleared(gate[key]):
            continue
        doc = _ref_document(lookup, project_id, table, ref.column, row)
        if doc is not None and cap.document_shipped(session, project_id, doc.get(_UID)):
            relaxed[key] = Sensitivity.INTERNAL.value
    return None if relaxed == gate else (gate, relaxed)


def withdrawn_hold(
    session: Session, project_id: int, table: str, row: Mapping[str, object]
) -> tuple[bool, frozenset[str]]:
    """Держит ли строку local только отозванный документ (решение 20 реестра).

    Чужая операция над такой строкой захвачена до отзыва (или на реплике,
    которая его ещё не видела); отвергнуть её — навсегда остановить pull этой
    реплики, записать — положить в отозванное. Применение её поглощает:
    ``(True, …)`` — строка целиком local из-за отозванного документа; второй
    элемент — колонки, которые придержаны только из-за него. Свой никогда не
    уезжавший restricted-документ сюда не попадает: гейт отвергает операцию
    как раньше.
    """
    pair = _relaxed(session, project_id, table, row)
    if pair is None:
        return False, frozenset()
    gate, relaxed = pair
    held = not cap._ships(table, gate) and cap._ships(table, relaxed)
    extra = reg.withheld_columns(table, gate) - reg.withheld_columns(table, relaxed)
    return held, frozenset(extra)


def ships_but_for_withdrawal(
    session: Session, project_id: int, table: str, row: Mapping[str, object]
) -> bool:
    """Сущность уезжала бы целиком, не будь отозванного документа (гейт ревизии).

    Ревизия такой сущности, пришедшая с чужой реплики, записана до отзыва:
    у получателей она уже есть, и после понижения войдёт в дайджест у всех.
    Применение её пишет, а не отвергает — она local здесь, как и сущность.
    """
    pair = _relaxed(session, project_id, table, row)
    if pair is None:
        return False
    _, relaxed = pair
    return cap._ships(table, relaxed) and not reg.withheld_columns(table, relaxed)


def restricted_by(session: Session, project_id: int, doc_uid: object) -> str | None:
    """uid реплики, чей отзыв держит документ restricted здесь; ``None`` — отзыва нет.

    Свой отзыв — последняя своя запись метки (решение 20): реплика сама
    повысила документ и только она вправе его понизить; чужой отзыв, пришедший
    позже, у неё поглощается и этого не меняет. Иначе — первый чужой отзыв
    после последней записи уезжающей метки (кто бы её ни писал): он и перевёл
    документ в restricted у этой реплики.
    """
    if not isinstance(doc_uid, str):
        return None
    ops = _ops_table()
    conn = session.connection()
    me = self_replica(conn, project_id)
    labels = [
        (replica_uid, fields)
        for replica_uid, raw in conn.execute(
            select(ops.c.replica_uid, ops.c.fields)
            .where(
                ops.c.project_id == project_id,
                ops.c.entity_kind == _DOCUMENT,
                ops.c.entity_uid == doc_uid,
                ops.c.kind != _DELETE,
            )
            .order_by(ops.c.hlc, ops.c.op_id)
        )
        if reg.WITHDRAW_COLUMN in (fields := _loads(raw))
    ]
    own = [fields for replica_uid, fields in labels if replica_uid == me]
    if own and reg.is_withdraw(_DOCUMENT, own[-1]):
        return me
    cleared = [i for i, (_, f) in enumerate(labels) if reg.is_cleared(f[reg.WITHDRAW_COLUMN])]
    start = cleared[-1] + 1 if cleared else 0
    for replica_uid, fields in labels[start:]:
        if replica_uid != me and reg.is_withdraw(_DOCUMENT, fields):
            return str(replica_uid)
    return None


def withdrawn_by_peer(session: Session, project_id: int, doc_uid: object) -> bool:
    """Документ restricted здесь по чужому отзыву (``restricted_by``).

    Тогда у этой реплики от документа осталась оболочка с затёртым телом, и
    понижение отсюда разослало бы пустые секции поверх настоящего текста.
    Понижать документ может реплика, которая отозвала его сама.
    """
    who = restricted_by(session, project_id, doc_uid)
    return who is not None and who != self_replica(session.connection(), project_id)
