"""Применение чужого changeset'а к локальной БД (RFC 29 §3.6, §10.6; CLO-013).

Одна транзакция на changeset — её открывает вызывающий (``pull``), здесь только
запись в переданную сессию. Всё под ``sync_capture.suspended``: применённая
операция не должна вернуться эхом в журнал и уехать следующим push'ем.

Что делает применение:

1. **uid → row_id.** Строка ищется по ``uid`` (у тег-таблиц и
   ``link_suggestion`` колонки нет — по колонкам ``deterministic_uid``, тем же,
   из которых считается uid ребра). FK приходят uid'ами конца (ключ — имя
   колонки, решение D11) и переводятся в локальный ``row_id``; полиморфная
   ссылка — по дискриминатору, как в захвате. Висячая логическая или
   полиморфная ссылка — ``NULL``, как её и сериализует захват; висячий
   настоящий FK — ``SYNC-102``: changeset ссылается на сущность, которой у
   реплики нет.
2. **Только уезжающие колонки.** Поля вне ``SHIPPED_COLUMNS`` (local, чужие,
   незнакомые) отбрасываются — remote не пишет ``root_path`` и ``position``.
3. **Создание.** Новая строка получает локальный ``row_id``; local-колонки с
   ``NOT NULL`` заполняются здесь: ``position`` — append-счётчиком (у
   адресных позиций — номер критерия, опции, диаграммы, D7) или пересчётом из
   ранга (``ordering.renumber_parent``) там, где ``position`` — индекс.
   Правка строки, которой у реплики нет (непустой ``base``), — ``SYNC-102``:
   её создание было до журнала, это работа clone и снапшота (CLO-029).
4. **Удаление** идемпотентно: строки нет (снёс ORM-каскад родителя этого же
   changeset'а) — пропуск.
5. **Derived** пересобирается по затронутым сущностям тем же кодом, что и на
   обычной записи: ``link_service.sync_section`` + ``resolve_section`` по
   секциям, ``search_service.index_*`` по документам, задачам, историям, ADR,
   находкам и вопросам (§3.6, «пересборка derived»).
6. **Журнал.** Чужие операции ложатся в ``sync_op`` с ``changeset = sha``: не
   хвост (push их не упакует), а история, на которую встанет LWW по полю
   секции C. Часы реплики видят ``hlc_max`` (``observe``): следующая своя
   операция будет позже применённых.

Чего здесь нет — слияния (секция C): применение пишет значения changeset'а
поверх локальных. ``pull`` зовёт его только для fast-forward, когда свой
неупакованный хвост этих сущностей не трогает.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import func, insert, select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra import ordering
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.hlc import Hlc, clock_for, self_replica
from cod_doc.infra.models import Base, SyncOpModel
from cod_doc.services import search_service
from cod_doc.services.sync_service import _local
from cod_doc.services.sync_service._values import from_wire
from cod_doc.services.sync_service.errors import ObjectMissingError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from sqlalchemy import Table
    from sqlalchemy.orm import Mapper, Session

    from cod_doc.infra.models.base import RankMixin
    from cod_doc.services.sync_service._local import ProjectRef
    from cod_doc.services.sync_service.changeset import Changeset, Op

logger = logging.getLogger("cod_doc.services.sync_service.apply")

_UID: Final = "uid"
_POSITION: Final = "position"
_PROJECT: Final = "project"
#: Где ``position`` — адрес (``<story_id>#<position>``, номер опции, диаграммы):
#: append-счётчик, не пересчитывается (DATA_MODEL §3.18.2, D7).
ADDRESS_POSITIONS: Final = frozenset({"story_acceptance", "open_question_option", "adr_diagram"})
#: Где ``position`` — индекс в списке, который читают запросы и выдачи:
#: пересчитывается из ранга после применения.
INDEX_POSITIONS: Final = frozenset({"section", "plan_section", "scenario_step"})
#: Local-колонки ``NOT NULL`` без дефолта, кроме ``position``: значение,
#: которое строка получает при создании с чужой реплики.
_LOCAL_FILL: Final[Mapping[tuple[str, str], object]] = {("link_suggestion", "score"): 0.0}
#: Порог ``IN (...)``: SQLite старше 3.32 держит 999 параметров.
_CHUNK: Final = 500


# --- метаданные -------------------------------------------------------------------


def _mapper(table: str) -> Mapper[object]:
    # link_suggestion регистрируется лениво (решение 1 реестра): модуль моделей
    # не импортирует её, а чужой changeset может принести решение по подсказке.
    import cod_doc.infra.models.link_suggestions  # noqa: F401

    for mapper in Base.registry.mappers:
        if cast("Table", mapper.local_table).name == table:
            return mapper
    msg = f"операция над таблицей {table!r}, которой нет в схеме реплики"
    raise ObjectMissingError(msg)


def _info(table: str) -> tuple[Mapper[object], cap.TableInfo]:
    if table not in cap.CAPTURED_TABLES:
        msg = f"операция над таблицей {table!r}, которая не синхронизируется"
        raise ObjectMissingError(msg)
    mapper = _mapper(table)
    info = cap.table_info(mapper)
    assert info is not None  # CAPTURED_TABLES → table_info не None
    return mapper, info


def fk_uids(op: Op) -> set[str]:
    """uid, на которые ссылаются FK-поля операции (для проверки расхождения)."""
    if op.entity_kind not in cap.CAPTURED_TABLES:
        return set()
    _, info = _info(op.entity_kind)
    refs = {*info.fk_targets, *info.polymorphic}
    payload = op.fields if op.kind == "upsert" else op.base
    return {str(v) for c, v in payload.items() if c in refs and isinstance(v, str)}


# --- затронутое -------------------------------------------------------------------


@dataclass
class Touched:
    """Что пересобрать после применения changeset'а."""

    doc_ids: set[int] = field(default_factory=set)
    unindexed_doc_keys: set[str] = field(default_factory=set)
    section_ids: set[int] = field(default_factory=set)
    #: (таблица, атрибут-идентичность, значение) — строки для ``index_*``.
    entities: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    #: FTS-строки удалённых сущностей: (kind, ref).
    fts_deleted: set[tuple[str, str]] = field(default_factory=set)
    #: Родители, у которых ``position`` пересчитывается из ранга.
    renumber: set[tuple[str, tuple[tuple[str, object], ...]]] = field(default_factory=set)


#: Таблица → (kind FTS, атрибут ref) для удаления строки индекса.
_FTS_REFS: Final[Mapping[str, tuple[str, str]]] = {
    "task": ("task", "task_id"),
    "user_story": ("story", "story_id"),
    "adr": ("adr", "adr_id"),
    "finding": ("finding", "finding_uid"),
    "open_question": ("question", "question_id"),
}
#: Дочерняя таблица → (родительская таблица FTS, FK на неё).
_FTS_PARENT: Final[Mapping[str, tuple[str, str]]] = {
    "story_acceptance": ("user_story", "story_id"),
    "open_question_option": ("open_question", "question_row_id"),
}


def _note(touched: Touched, table: str, obj: object, *, deleted: bool, op: Op) -> None:
    if table == "document":
        if deleted:
            touched.unindexed_doc_keys.add(str(getattr(obj, "doc_key")))  # noqa: B009
        else:
            touched.doc_ids.add(int(getattr(obj, "row_id")))  # noqa: B009
            old_key = op.base.get("doc_key")
            if isinstance(old_key, str) and old_key != getattr(obj, "doc_key"):  # noqa: B009
                touched.unindexed_doc_keys.add(old_key)
    elif table == "section":
        touched.doc_ids.add(int(getattr(obj, "document_id")))  # noqa: B009
        if not deleted:
            touched.section_ids.add(int(getattr(obj, "row_id")))  # noqa: B009
    elif table in _FTS_REFS:
        if deleted:
            kind, attr = _FTS_REFS[table]
            touched.fts_deleted.add((kind, str(getattr(obj, attr))))
        else:
            touched.entities[table].add(int(getattr(obj, "row_id")))  # noqa: B009
    elif table in _FTS_PARENT:
        parent, column = _FTS_PARENT[table]
        touched.entities[parent].add(int(getattr(obj, column)))
    if table in INDEX_POSITIONS:
        parent_cols = ordering.PARENTS[table]
        touched.renumber.add((table, tuple((c, getattr(obj, c)) for c in parent_cols)))


# --- значения ---------------------------------------------------------------------


def _row_id_by_uid(session: Session, table: str, uid: str) -> int | None:
    tbl = cast("Table", _mapper(table).local_table)
    found = session.execute(select(tbl.c.row_id).where(tbl.c.uid == uid)).scalar_one_or_none()
    return None if found is None else int(found)


def _translate(
    session: Session,
    info: cap.TableInfo,
    payload: Mapping[str, object],
    existing: object | None,
) -> dict[str, object]:
    """Поля операции → значения ORM-атрибутов: FK uid → row_id, время → datetime."""
    out: dict[str, object] = {}
    for column, value in payload.items():
        if column not in info.shipped:
            continue  # local, чужая или незнакомая колонка: remote её не пишет
        out[column] = _value(session, info, column, value, payload, existing)
    return out


def _value(
    session: Session,
    info: cap.TableInfo,
    column: str,
    value: object,
    payload: Mapping[str, object],
    existing: object | None,
) -> object:
    if value is None:
        return None
    if column in info.fk_targets:
        target = info.fk_targets[column]
        row_id = _row_id_by_uid(session, target, str(value))
        if row_id is None and column not in info.logical:
            msg = f"{info.table}.{column}: на реплике нет строки {target} с uid {value}"
            raise ObjectMissingError(msg)
        return row_id  # висячая логическая ссылка — NULL, как в захвате
    if column in info.polymorphic:
        disc = info.polymorphic[column]
        kind = payload.get(disc, getattr(existing, info.attr(disc), None) if existing else None)
        try:
            poly_target = reg.ENTITY_KIND_TABLES.get(EntityKind(str(kind)))
        except ValueError:
            poly_target = None
        if poly_target is None or poly_target not in reg.UID_TABLES:
            msg = f"{info.table}.{column}: вид сущности {kind!r} без uid"
            raise ObjectMissingError(msg)
        return _row_id_by_uid(session, poly_target, str(value))
    return from_wire(info.table, column, value)


# --- поиск строки -----------------------------------------------------------------


def _find(
    session: Session,
    mapper: Mapper[object],
    info: cap.TableInfo,
    op: Op,
    key_payload: Mapping[str, object],
) -> object | None:
    model = mapper.class_
    if info.has_uid:
        stmt = select(model).where(getattr(model, info.attr(_UID)) == op.entity_uid)
        return session.scalars(stmt).one_or_none()
    columns = reg.spec_for(info.table).deterministic_uid or ()
    try:
        values = _translate(session, info, {c: key_payload.get(c) for c in columns}, None)
    except ObjectMissingError:
        return None  # конца ребра нет — нет и ребра; создание упадёт на полях
    if any(values.get(c) is None and c not in info.nullable for c in columns):
        return None  # конец ребра не найден — и строки нет
    stmt = select(model)
    for column in columns:
        attr = getattr(model, info.attr(column))
        value = values.get(column)
        stmt = stmt.where(attr.is_(None) if value is None else attr == value)
    return session.scalars(stmt).one_or_none()


# --- создание ---------------------------------------------------------------------


def _fill_local(session: Session, mapper: Mapper[object], info: cap.TableInfo, obj: object) -> None:
    """Local-колонки ``NOT NULL`` новой строки: позиция, известные заглушки."""
    tbl = cast("Table", mapper.local_table)
    for column in tbl.columns:
        name = column.name
        if name in info.shipped or name in {"row_id", _UID}:
            continue
        if getattr(obj, info.attr(name)) is not None:
            continue
        if column.nullable or column.default is not None or column.server_default is not None:
            continue
        if name == _POSITION:
            setattr(obj, info.attr(name), _append_position(session, mapper, info, obj))
        elif (info.table, name) in _LOCAL_FILL:
            setattr(obj, info.attr(name), _LOCAL_FILL[(info.table, name)])
        else:
            msg = f"{info.table}.{name}: local-колонка NOT NULL без правила заполнения"
            raise AssertionError(msg)


def _append_position(
    session: Session, mapper: Mapper[object], info: cap.TableInfo, obj: object
) -> int:
    """Следующий номер у родителя: адрес не переиспользуется, индекс пересчитают."""
    tbl = cast("Table", mapper.local_table)
    stmt = select(func.max(tbl.c.position))
    for column in ordering.PARENTS.get(info.table, ()):
        stmt = stmt.where(tbl.c[column] == getattr(obj, info.attr(column)))
    last = session.execute(stmt).scalar_one_or_none()
    return 0 if last is None else int(last) + 1


# --- операции ---------------------------------------------------------------------


def _apply_op(session: Session, project: ProjectRef, op: Op, touched: Touched) -> None:
    mapper, info = _info(op.entity_kind)
    if op.entity_kind == _PROJECT and (op.entity_uid != project.uid or op.kind == "delete"):
        msg = f"операция над проектом {op.entity_uid} в changeset'е проекта {project.uid}"
        raise ObjectMissingError(msg)
    if op.kind == "delete":
        obj = _find(session, mapper, info, op, op.base)
        if obj is not None:
            _note(touched, info.table, obj, deleted=True, op=op)
            session.delete(obj)
        return
    obj = _find(session, mapper, info, op, op.fields)
    values = _translate(session, info, op.fields, obj)
    if obj is None:
        if op.base:
            msg = (
                f"правка {info.table} {op.entity_uid}, которой на реплике нет: "
                "её создание старше журнала — нужен clone со снапшота (CLO-029)"
            )
            raise ObjectMissingError(msg)
        obj = mapper.class_()
        if info.has_uid:
            setattr(obj, info.attr(_UID), op.entity_uid)
        for column, value in values.items():
            setattr(obj, info.attr(column), value)
        _fill_local(session, mapper, info, obj)
        session.add(obj)
    else:
        for column, value in values.items():
            setattr(obj, info.attr(column), value)
    session.flush()
    _note(touched, info.table, obj, deleted=False, op=op)


# --- derived ----------------------------------------------------------------------


def _renumber(session: Session, touched: Touched) -> None:
    for table, parent in sorted(touched.renumber, key=repr):
        model = cast("type[RankMixin]", _mapper(table).class_)
        where: dict[str, Any] = dict(parent)
        ordering.renumber_parent(session, model, **where)


def _rebuild_links(session: Session, section_ids: Iterable[int]) -> None:
    """Ссылки секций — как ``doc_service._sync_section_links_safe``: best-effort.

    Derived: сбой не откатывает применённый changeset, а оставляет ссылки
    секции до ``cod-doc link backfill``. SAVEPOINT — чтобы сбой не испортил
    транзакцию применения.
    """
    from cod_doc.infra.models import SectionModel
    from cod_doc.services import link_service

    for section_id in sorted(section_ids):
        if session.get(SectionModel, section_id) is None:
            continue
        try:
            with session.begin_nested():
                link_service.sync_section(session, section_id)
                link_service.resolve_section(session, section_id)
        except Exception:
            logger.warning(
                "pull: ссылки секции %s не пересобраны — `cod-doc link backfill`",
                section_id,
                exc_info=True,
            )


def _indexers() -> Mapping[str, tuple[type[object], Callable[[Session, Any], None]]]:
    """Таблица → (модель, ``search_service.index_*``) — те же хуки, что у write-путей."""
    from cod_doc.infra.models import (
        ADRModel,
        FindingModel,
        OpenQuestionModel,
        TaskModel,
        UserStoryModel,
    )

    return {
        "task": (TaskModel, search_service.index_task),
        "user_story": (UserStoryModel, search_service.index_story),
        "adr": (ADRModel, search_service.index_adr),
        "finding": (FindingModel, search_service.index_finding),
        "open_question": (OpenQuestionModel, search_service.index_question),
    }


def _rebuild_fts(session: Session, project: ProjectRef, touched: Touched) -> None:
    from cod_doc.infra.models import DocumentModel

    for kind, ref in sorted(touched.fts_deleted):
        try:
            search_service.delete_entity(session, kind=kind, ref=ref, project_id=project.row_id)
        except search_service.SearchIndexMissing:
            logger.warning("pull: db_search_idx нет — %s %s не снят с индекса", kind, ref)
    for doc_key in sorted(touched.unindexed_doc_keys):
        search_service.unindex_doc(session, project_id=project.row_id, doc_key=doc_key)
    for doc_id in sorted(touched.doc_ids):
        doc = session.get(DocumentModel, doc_id)
        if doc is not None:
            search_service.index_doc(session, project_id=project.row_id, doc_key=doc.doc_key)
    for table, (model, index) in _indexers().items():
        for row_id in sorted(touched.entities.get(table, ())):
            row = session.get(model, row_id)
            if row is not None:
                index(session, row)


# --- журнал -----------------------------------------------------------------------


def _record_ops(session: Session, project: ProjectRef, sha: str, cs: Changeset) -> None:
    ids = [op.op_id for op in cs.ops]
    known: set[str] = set()
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        known |= set(session.scalars(select(SyncOpModel.op_id).where(SyncOpModel.op_id.in_(chunk))))
    rows = [
        {
            "op_id": op.op_id,
            "project_id": project.row_id,
            "replica_uid": Hlc.parse(op.hlc).replica_uid,
            "hlc": op.hlc,
            "entity_kind": op.entity_kind,
            "entity_uid": op.entity_uid,
            "kind": op.kind,
            "fields": _dumps(op.fields),
            "base": _dumps(op.base),
            "author": op.author,
            "changeset": sha,
        }
        for op in cs.ops
        if op.op_id not in known
    ]
    if rows:
        session.execute(insert(SyncOpModel), rows)


def _dumps(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# --- вход -------------------------------------------------------------------------


def apply_changeset(session: Session, project: ProjectRef, sha: str, cs: Changeset) -> int:
    """Применить ``cs`` в транзакции ``session``; вернуть число операций.

    Сбой любой операции — исключение, и вызывающий откатывает транзакцию
    целиком: changeset применяется весь или никак.
    """
    touched = Touched()
    with cap.suspended(session):
        for op in cs.ops:
            _apply_op(session, project, op, touched)
        _renumber(session, touched)
        _rebuild_links(session, touched.section_ids)
        _rebuild_fts(session, project, touched)
    _record_ops(session, project, sha, cs)
    _local.record_changeset(session, project, sha, cs)
    if cs.ops:
        clock_for(session, self_replica(session, project.row_id)).observe(Hlc.parse(cs.hlc_max))
    return len(cs.ops)
