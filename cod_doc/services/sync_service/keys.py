"""Совпавший натуральный ключ при вставке: склейка и перебуквление (RFC 29 §3.3, Q-007; CLO-023).

Аренда защищает только номера; у части сущностей ключ — имя, которое придумывает
человек (``natural_keys`` реестра). Одно имя на двух репликах получает разные
uid, и чужая вставка упирается в UNIQUE своей строки. ``apply`` находит такую
строку до вставки (``_natural_twin``) и зовёт ``resolve_key``. Правило — по виду
ключа:

- **имя-смысл** (``KeyKind.NAME``: ``doc_key``, ``anchor`` в документе,
  ``node_key``, ``routine.name``…) — одно имя = одно намерение, строки
  склеиваются;
- **метка-порядок** (``KeyKind.LABEL``: ``plan_section.letter``) — буква ничего
  не значит, вторая перебуквляется;
- **арендованный номер** (``KeyKind.LEASED``) по построению не совпадает;
  совпадение провизорных — закрепление CLO-030 (``MergeRequiredError``).

**Кто первый.** Порядок — ``(hlc создания, uid)``: HLC первой операции строки в
журнале (вставка, ``base`` пуст), ничью решает uid. Сравнивают одни и те же два
штампа, поэтому выбор одинаков на любой реплике и в любой очерёдности pull/push.
Своя строка без операции создания в журнале (заведена до журнала, например
``project add`` до clone) никуда не уезжала, и её uid не знает никто — она
считается последней.

**Склейка.** Выживает первый uid. Своя строка остаётся той же строкой (``row_id``
и все ссылки на неё внутри реплики не трогаются); если выжил чужой uid, ей
переписывается ``uid``. Проигравший uid пишется в ``entity_alias`` (``reason =
merged``, событие ``sync.alias_created``): операции, собранные до склейки,
ссылаются на него, и ``apply`` резолвит их через алиас. Поля сливаются по §3.7:
по каждому полю побеждает больший штамп ``(hlc, op_id)`` — последней своей записи
поля в журнале против чужой вставки; своё поле без штампа (строка до журнала)
проигрывает. Тела (``text_columns`` реестра) без общего предка: равные
сливаются без следа, разные дают конфликт ``key_merge`` с обеими версиями, в
БД — победитель LWW.

Если проигравший uid — свой и ещё не уехал, свой хвост переписывается на
выжившего: ``entity_uid`` операций и FK-ссылки на него в чужих операциях хвоста
(uid ребра пересчитывается). Так проигравший uid не покидает реплику, а правки,
сделанные до склейки, видны LWW следующих чужих правок выжившего. Строка, уже
уехавшая под проигравшим uid, склеится на каждой реплике сама — по тому же
правилу.

**Перебуквление.** Вторая по ``(hlc, uid)`` секция получает следующую свободную
букву плана (``A``…``Z``, ``AA``…), слаг ``<буква>-…`` — вместе с ней; задачи
остаются при своих секциях. Запись — под ``suspended`` на обеих репликах: каждая
перебуквливает сама, когда видит чужую вставку, и пишет одинаковое событие
``plan_section.relettered`` ``{from, to, section_uid}``. Своя операция вставки в
хвосте не переписывается — по ней перебуквит и другая сторона.

Конфликт ``key_merge`` — local, как любой (§3.2): он открывается на реплике,
которая склеивала. uid конфликта детерминирован (``conflicts.open_conflict``):
если склейку видят обе реплики, uid у них один.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import select, update

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.models import (
    Base,
    EntityAliasModel,
    ProjectModel,
    SyncConflictModel,
    SyncOpModel,
)
from cod_doc.services import activity_service
from cod_doc.services.sync_service import _local, apply
from cod_doc.services.sync_service._values import to_wire
from cod_doc.services.sync_service.changeset import FieldValue, canonical_json
from cod_doc.services.sync_service.errors import MergeRequiredError, ObjectMissingError
from cod_doc.services.sync_service.merge import KEY_MERGE, ConflictSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service.changeset import Op
    from cod_doc.services.sync_service.merge import Context, KeyClash

__all__ = ["next_label", "resolve_key"]

_UID: Final = "uid"
_SLUG: Final = "slug"
_MERGED: Final = "merged"
_EMPTY: Final = "{}"
#: Штамп своей строки без операции создания: позже любого настоящего HLC.
_NEVER: Final = "￿"
_ALPHABET: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
#: Буква секции — ``^[A-Z]{1,2}$`` (``validate_plan_section_letter``).
_MAX_LABEL: Final = 2

type Stamp = tuple[str, str]


# --- общее ------------------------------------------------------------------------


def _project(session: Session, project_id: int) -> _local.ProjectRef:
    proj = session.get_one(ProjectModel, project_id)
    return _local.ProjectRef(row_id=proj.row_id, uid=str(proj.uid), slug=proj.slug)


def _row_of(info: cap.TableInfo, obj: object) -> dict[str, object]:
    return {column: getattr(obj, attr) for column, attr in info.columns}


def _equal(a: object, b: object) -> bool:
    return canonical_json(cast("FieldValue", a)) == canonical_json(cast("FieldValue", b))


def _ops(session: Session, project_id: int, table: str, uid: str) -> list[SyncOpModel]:
    return list(
        session.scalars(
            select(SyncOpModel)
            .where(
                SyncOpModel.project_id == project_id,
                SyncOpModel.entity_kind == table,
                SyncOpModel.entity_uid == uid,
            )
            .order_by(SyncOpModel.hlc, SyncOpModel.op_id)
        )
    )


def _created(rows: list[SyncOpModel], uid: str) -> Stamp:
    """``(hlc создания, uid)``; без операции создания — позже всех."""
    for row in rows:
        if row.kind == "upsert" and row.base == _EMPTY:
            return row.hlc, uid
    return _NEVER, uid


def _key(table: str, columns: tuple[str, ...]) -> reg.NaturalKey:
    for key in reg.spec_for(table).natural_keys:
        if key.columns == columns:
            return key
    msg = f"{table}: натурального ключа {columns} в реестре нет"
    raise LookupError(msg)


def resolve_key(session: Session, ctx: Context, clash: KeyClash) -> bool:
    """Стратегия ``key_merge`` (``merge.key_collision``).

    ``True`` — чужую вставку ``apply`` применяет как обычно (своя строка
    уступила букву). ``False`` — стратегия всё сделала сама: склеила или
    вставила чужую строку под новой буквой.
    """
    key = _key(clash.table, clash.key)
    if key.kind is reg.KeyKind.LEASED:
        msg = f"совпал арендованный номер {clash.table} {clash.key}"
        raise MergeRequiredError(msg, task="CLO-030")
    project = _project(session, ctx.project_id)
    found = apply.entity_by_uid(session, project, clash.table, clash.ours_uid)
    if found is None:  # _natural_twin только что её нашёл
        msg = f"{clash.table} {clash.ours_uid}: строки с совпавшим ключом нет"
        raise ObjectMissingError(msg)
    obj, info = found
    local = cap.shipped_fields(session, clash.table, _row_of(info, obj))
    if local is None:
        msg = (
            f"{clash.table} {clash.theirs.entity_uid}: ключ {clash.key} совпал со строкой, "
            "которую реестр этой реплики держит local (restricted)"
        )
        raise ObjectMissingError(msg)
    history = _ops(session, ctx.project_id, clash.table, clash.ours_uid)
    theirs = clash.theirs
    theirs_first = (theirs.hlc, theirs.entity_uid) < _created(history, clash.ours_uid)
    if key.kind is reg.KeyKind.LABEL:
        return _reletter(session, ctx, project, obj, info, key, theirs, theirs_first=theirs_first)
    _glue(
        session,
        ctx,
        project,
        obj,
        info,
        theirs,
        ours_uid=clash.ours_uid,
        local=to_wire(clash.table, local),
        history=history,
        theirs_first=theirs_first,
    )
    return False


# --- склейка ----------------------------------------------------------------------


def _stamps(history: list[SyncOpModel]) -> dict[str, Stamp]:
    """Последний штамп каждого поля своей строки по журналу (хвост и история)."""
    out: dict[str, Stamp] = {}
    for row in history:
        if row.kind != "upsert":
            continue
        for column in json.loads(row.fields):
            stamp = (row.hlc, row.op_id)
            if column not in out or stamp > out[column]:
                out[column] = stamp
    return out


def _glue(
    session: Session,
    ctx: Context,
    project: _local.ProjectRef,
    obj: object,
    info: cap.TableInfo,
    theirs: Op,
    *,
    ours_uid: str,
    local: Mapping[str, FieldValue],
    history: list[SyncOpModel],
    theirs_first: bool,
) -> None:
    table = info.table
    stamps = _stamps(history)
    theirs_stamp = (theirs.hlc, theirs.op_id)
    text_columns = reg.spec_for(table).text_columns
    winners: dict[str, FieldValue] = {}
    ours_text: dict[str, FieldValue] = {}
    theirs_text: dict[str, FieldValue] = {}
    for column, value in sorted(theirs.fields.items()):
        mine = local.get(column)
        if _equal(mine, value):
            continue
        if column in text_columns:
            ours_text[column] = mine
            theirs_text[column] = value
        stamp = stamps.get(column)
        if stamp is None or theirs_stamp > stamp:
            winners[column] = value
    if theirs_first:
        survivor, loser = theirs.entity_uid, ours_uid
        setattr(obj, info.attr(_UID), survivor)
    else:
        survivor, loser = ours_uid, theirs.entity_uid
    if winners:
        for column, orm in apply.orm_values(session, project, info, winners, obj).items():
            setattr(obj, info.attr(column), orm)
    # Тот же финальный гейт, что у обычной вставки: проект строки и реестр
    # (restricted, придержанные колонки) — по итоговой строке и записанным полям.
    apply.finish_row(session, project, info, obj, survivor, winners)
    if theirs_first:
        _rekey_tail(session, ctx.project_id, table, loser, survivor)
        _repoint_conflicts(session, ctx, table, loser, survivor)
    _alias(session, ctx, table, loser, survivor)
    if ours_text:
        ctx.conflicts.append(
            ConflictSpec(
                entity_kind=table,
                entity_uid=survivor,
                field=None,
                kind=KEY_MERGE,
                ours={"uid": ours_uid, "fields": ours_text},
                theirs={"uid": theirs.entity_uid, "fields": theirs_text},
                base=None,
                theirs_op=theirs.op_id,
            )
        )


def _repoint_conflicts(
    session: Session, ctx: Context, table: str, loser: str, survivor: str
) -> None:
    """Конфликты своей строки — на выжившего, когда её uid проиграл (CLO-027, сид 91).

    Три двойника: первая склейка открыла конфликт на своём uid, вторая этот uid
    переписала в алиас. Конфликт на алиасе — другая подпись, чем у реплик,
    которые этого uid не видели (свой проигравший uid не уезжает), а его
    разрешение писало бы операцию под uid, который знает только эта реплика.
    """
    ctx.conflicts = [
        dataclasses.replace(spec, entity_uid=survivor)
        if (spec.entity_kind, spec.entity_uid) == (table, loser)
        else spec
        for spec in ctx.conflicts
    ]
    session.execute(
        update(SyncConflictModel)
        .where(
            SyncConflictModel.project_id == ctx.project_id,
            SyncConflictModel.entity_kind == table,
            SyncConflictModel.entity_uid == loser,
            SyncConflictModel.status == "open",
        )
        .values(entity_uid=survivor)
    )


def _alias(session: Session, ctx: Context, table: str, loser: str, survivor: str) -> None:
    if session.get(EntityAliasModel, (ctx.project_id, table, loser)) is None:
        session.add(
            EntityAliasModel(
                project_id=ctx.project_id,
                entity_kind=table,
                alias=loser,
                uid=survivor,
                reason=_MERGED,
            )
        )
        session.flush()
    activity_service.emit_for_write(
        session,
        ctx.project_id,
        "sync.alias_created",
        ctx.author,
        scope_kind=table,
        scope_id=survivor,
        payload={"alias": loser, "uid": survivor, "reason": _MERGED},
        summary=f"склейка {table}: {loser} → {survivor}",
    )


def _refs_to(table: str, target: str) -> tuple[frozenset[str], dict[str, str]]:
    """FK-колонки таблицы, указывающие на ``target``, и полиморфные (колонка → дискриминатор)."""
    mapper = apply._mapper(table)
    info = cap.table_info(mapper)
    if info is None:
        return frozenset(), {}
    direct = frozenset(c for c, t in info.fk_targets.items() if t == target)
    return direct, dict(info.polymorphic)


def _points_to(
    payload: Mapping[str, object],
    column: str,
    loser: str,
    target: str,
    poly: Mapping[str, str],
    direct: frozenset[str],
) -> bool:
    if payload.get(column) != loser:
        return False
    if column in direct:
        return True
    disc = poly.get(column)
    if disc is None:
        return False
    kind = payload.get(disc)
    return any(str(k.value) == kind and t == target for k, t in reg.ENTITY_KIND_TABLES.items())


def _rekey_tail(session: Session, project_id: int, table: str, loser: str, survivor: str) -> None:
    """Свой хвост — на выжившего: ``entity_uid`` и ссылки; история — только ``entity_uid``.

    История (упакованные операции) больше не уезжает; переписать её
    ``entity_uid`` нужно затем, чтобы LWW следующих чужих правок выжившего
    видел свои штампы полей.
    """
    session.execute(
        update(SyncOpModel)
        .where(
            SyncOpModel.project_id == project_id,
            SyncOpModel.entity_kind == table,
            SyncOpModel.entity_uid == loser,
        )
        .values(entity_uid=survivor)
    )
    tail = session.scalars(
        select(SyncOpModel).where(
            SyncOpModel.project_id == project_id, SyncOpModel.changeset.is_(None)
        )
    )
    for row in tail:
        if row.entity_kind not in cap.CAPTURED_TABLES:
            continue
        direct, poly = _refs_to(row.entity_kind, table)
        changed = False
        payloads = {}
        for name in ("fields", "base"):
            payload = json.loads(getattr(row, name))
            for column in [*direct, *poly]:
                if _points_to(payload, column, loser, table, poly, direct):
                    payload[column] = survivor
                    changed = True
            payloads[name] = payload
        if not changed:
            continue
        row.fields = _dumps(payloads["fields"])
        row.base = _dumps(payloads["base"])
        if reg.spec_for(row.entity_kind).deterministic_uid is not None:
            source = payloads["fields"] if row.kind == "upsert" else payloads["base"]
            # В операции нет всех концов ребра — uid не пересчитать.
            with contextlib.suppress(KeyError):
                row.entity_uid = reg.edge_uid(row.entity_kind, source)
    session.flush()


def _dumps(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# --- перебуквление ---------------------------------------------------------------


def _labels() -> list[str]:
    one = list(_ALPHABET)
    return one + [a + b for a in _ALPHABET for b in _ALPHABET][: 26**_MAX_LABEL]


def next_label(current: str, used: set[str]) -> str:
    """Следующая свободная буква после ``current``: ``A``…``Z``, затем ``AA``…``ZZ``."""
    labels = _labels()
    start = labels.index(current) + 1 if current in labels else 0
    for label in labels[start:]:
        if label not in used:
            return label
    msg = f"свободной буквы после {current!r} нет"
    raise ValueError(msg)


def _reletter(
    session: Session,
    ctx: Context,
    project: _local.ProjectRef,
    obj: object,
    info: cap.TableInfo,
    key: reg.NaturalKey,
    theirs: Op,
    *,
    theirs_first: bool,
) -> bool:
    *parents, column = key.columns
    tbl = Base.metadata.tables[info.table]
    where = [tbl.c[p] == getattr(obj, info.attr(p)) for p in parents]
    used = {str(v) for v in session.scalars(select(tbl.c[column]).where(*where))}
    old = str(theirs.fields[column])
    new = next_label(old, used)
    if theirs_first:
        # Своя уступает: новая буква ей, чужая вставка идёт как обычно.
        uid = str(getattr(obj, info.attr(_UID)))
        setattr(obj, info.attr(column), new)
        slug = getattr(obj, info.attr(_SLUG), None) if _SLUG in info.shipped else None
        if isinstance(slug, str) and slug.startswith(f"{old}-"):
            setattr(obj, info.attr(_SLUG), new + slug[len(old) :])
        session.flush()
    else:
        uid = theirs.entity_uid
        fields: dict[str, object] = {**theirs.fields, column: new}
        slug = fields.get(_SLUG)
        if isinstance(slug, str) and slug.startswith(f"{old}-"):
            fields[_SLUG] = new + slug[len(old) :]
        apply.create_row(session, project, info.table, uid, fields)
    parent_uid = _parent_uid(session, info, obj, parents)
    activity_service.emit_for_write(
        session,
        ctx.project_id,
        f"{info.table}.relettered",
        ctx.author,
        scope_kind="plan",
        scope_id=parent_uid,
        payload={"from": old, "to": new, "section_uid": uid},
        summary=f"{info.table} {uid}: {old} → {new}",
    )
    return theirs_first


def _parent_uid(session: Session, info: cap.TableInfo, obj: object, parents: list[str]) -> str:
    for column in parents:
        target = info.fk_targets.get(column)
        if target is None or target not in reg.UID_TABLES:
            continue
        tbl = Base.metadata.tables[target]
        value = getattr(obj, info.attr(column))
        found = session.scalars(select(tbl.c.uid).where(tbl.c.row_id == value)).first()
        if found is not None:
            return str(found)
    return ""
