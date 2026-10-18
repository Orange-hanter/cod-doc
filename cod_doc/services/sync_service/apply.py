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
2. **Недоверенный ввод.** Changeset собирает тот, кто пишет в remote, а
   hub-БД держит несколько проектов. Поэтому: строка по uid и конец FK
   ищутся только в проекте pull'а (``project_id`` или цепочка родителей) —
   чужой проект — SYNC-102; итоговая строка сверяется ещё раз, так что
   ``project_id`` на чужой проект не перевешивается; поле вне
   ``SHIPPED_COLUMNS`` (local, ``uid``, незнакомое) и таблица вне захвата —
   SYNC-102; строка, которую реестр этой реплики держит local (restricted и
   всё под ним, придержанные колонки), не пишется ни до, ни после правки;
   штампы проверяет ``check_clock`` (реплика операций, не своя реплика, не
   дальше ``hlc.MAX_DRIFT_MS`` впереди стенных часов). Любой отказ —
   исключение, и транзакция changeset'а откатывается целиком.
3. **Создание.** Новая строка получает локальный ``row_id``; local-колонки с
   ``NOT NULL`` заполняются здесь: ``position`` — append-счётчиком (у
   адресных позиций — номер критерия, опции, диаграммы, D7) или пересчётом из
   ранга (``ordering.renumber_parent``) там, где ``position`` — индекс.
   Правка строки, которой у реплики нет (непустой ``base``), — ``SYNC-102``:
   её создание было до журнала, это работа clone и снапшота (CLO-029).
4. **Удаление** идемпотентно: строки нет (снёс ORM-каскад родителя этого же
   changeset'а) — пропуск. Удаление родителя — одна операция, поддерево
   уходит каскадом на каждой реплике (Q-008); до ``session.delete``
   поддерево обходится по ``CASCADE_PATHS``. Отказ (SYNC-102), если каскад
   снёс бы строку другого проекта или строку, которую реестр держит local по
   метке документа (пути ``gated``: ``section``, ``doc_comment``,
   ``document_tag`` restricted-документа, ``story_link``/``scenario_link``/
   ``open_question_link`` на него, ``approval_doc_revision_link`` его
   ревизии, ``plan_section``). Пути ``owned`` уходят с родителем, даже если
   ребёнок local: ``affected_file``/``module_code`` с машинным путём,
   ``task_metrics``, ``trace_call``, ``routine_run``, ``finding_source_run``,
   derived ``link`` и ``doc_node_suggestion``. Если поддерево задевает свой
   хвост, ``tombstone.before_delete`` снимает его в конфликт ``delete_edit``
   (CLO-022); чужая операция над потомком своего удалённого родителя — в тот же
   снимок (``tombstone.absorb_orphan``), не в SYNC-102.
5. **Derived** пересобирается по затронутым сущностям тем же кодом, что и на
   обычной записи: ``link_service.sync_section`` + ``resolve_section`` по
   секциям, ``search_service.index_*`` по документам, задачам, историям, ADR,
   находкам и вопросам (§3.6, «пересборка derived»).
6. **Журнал операций.** Чужие операции ложатся в ``sync_op`` с ``changeset = sha``: не
   хвост (push их не упакует), а история, на которую встанет LWW по полю
   секции C. Часы реплики видят ``hlc_max`` (``observe``): следующая своя
   операция будет позже применённых.

7. **Слияние** (секция C, ``merge``). Правка существующей строки пишет только
   поля, выигравшие LWW у хвоста и истории реплики (``merge.fields_to_apply``);
   удаление против правки или ссылки решает стратегия вида (``merge.allow``),
   совпадение натурального ключа при вставке — ``merge.key_collision``. Поле
   вне ``SHIPPED_COLUMNS`` отвергается до слияния: отброшенное слиянием поле
   всё равно проходит гейт недоверенного ввода.
8. **Journal-таблицы** (``revision``, CLO-024) не сливаются: операцию
   применяет ``journal.apply_op`` — объединение по ``revision_id``, родитель —
   локальная голова сущности (Q-010).
9. **Отзыв restricted-документа** (CLO-046, решение 20 реестра). Операция
   отзыва (``reg.is_withdraw``: документ, одно поле ``sensitivity``) проходит
   гейт до записи как обычная правка, а после записи строка законно local —
   финальный гейт её не судит (``_finish_withdraw``); тело затирается, свой
   хвост по документу вычищается (``sync_withdraw.withdraw_locally``).
   Операция над строкой, которую держит local только отозванный документ
   (restricted здесь и бывал в changeset'е), поглощается — не пишется и не
   отвергается (``_drop_withdrawn``): она захвачена до отзыва, а отказ навсегда
   остановил бы pull. Понижение отозванного документа гейт до записи судит по
   строке уже с новой меткой (``_lowers_shared_document``).
"""

from __future__ import annotations

import dataclasses
import functools
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import func, insert, select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra import ordering
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra import sync_withdraw as cap_withdraw
from cod_doc.infra.hlc import MAX_DRIFT_MS, Hlc, clock_for, self_replica, wall_ms
from cod_doc.infra.models import Base, EntityAliasModel, SyncConflictModel, SyncOpModel
from cod_doc.services import search_service
from cod_doc.services.sync_service import _local, merge
from cod_doc.services.sync_service._values import from_wire
from cod_doc.services.sync_service.errors import ObjectMissingError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from sqlalchemy import Table
    from sqlalchemy.orm import Mapper, Session
    from sqlalchemy.sql.elements import ColumnElement

    from cod_doc.infra.models.base import RankMixin
    from cod_doc.services.sync_service._local import ProjectRef
    from cod_doc.services.sync_service.changeset import Changeset, Op

logger = logging.getLogger("cod_doc.services.sync_service.apply")

_UID: Final = "uid"
_POSITION: Final = "position"
_PROJECT: Final = "project"
_PROJECT_ID: Final = "project_id"
_ROW_ID: Final = "row_id"
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


def _note(
    touched: Touched, table: str, obj: object, *, deleted: bool, base: Mapping[str, object]
) -> None:
    if table == "document":
        if deleted:
            touched.unindexed_doc_keys.add(str(getattr(obj, "doc_key")))  # noqa: B009
        else:
            touched.doc_ids.add(int(getattr(obj, "row_id")))  # noqa: B009
            old_key = base.get("doc_key")
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


# --- проект строки ----------------------------------------------------------------


def _row(session: Session, table: str, where: ColumnElement[bool]) -> dict[str, object] | None:
    tbl = Base.metadata.tables[table]
    found = session.execute(select(tbl).where(where)).mappings().first()
    return None if found is None else dict(found)


def _project_of(session: Session, table: str, row: Mapping[str, object]) -> object:
    """``project.row_id`` строки: своя ``project_id`` или цепочка родителей захвата.

    Hub-БД держит несколько проектов, а uid и FK из changeset'а — недоверенный
    ввод: строка, найденная по uid, обязана принадлежать проекту pull'а.
    """
    for _ in range(len(cap.CAPTURED_TABLES) + 1):
        if table == _PROJECT:
            return row.get(_ROW_ID)
        if _PROJECT_ID in row:
            return row[_PROJECT_ID]
        info = cap.table_info(_mapper(table))
        if info is None or info.parent is None:
            return None
        column, target = info.parent
        value = row.get(column)
        if value is None:
            return None
        parent = _row(session, target, Base.metadata.tables[target].c.row_id == value)
        if parent is None:
            return None
        table, row = target, parent
    return None


def _require_own(
    session: Session, project: ProjectRef, table: str, row: Mapping[str, object], what: str
) -> None:
    owner = _project_of(session, table, row)
    if owner != project.row_id:
        msg = f"{what}: строка {table} принадлежит другому проекту этой БД"
        raise ObjectMissingError(msg)


def _row_of(info: cap.TableInfo, obj: object) -> dict[str, object]:
    return {column: getattr(obj, attr) for column, attr in info.columns}


# --- значения ---------------------------------------------------------------------


def alias_target(session: Session, project_id: int, table: str, uid: str) -> str | None:
    """Живой uid склеенной сущности (``entity_alias``, CLO-023); ``None`` — алиаса нет.

    Цепочка алиасов (выживший сам позже склеен) проходится до конца; цикл
    обрывает множество посещённых.
    """
    found: str | None = None
    seen = {uid}
    current = uid
    while True:
        row = session.get(EntityAliasModel, (project_id, table, current))
        if row is None or row.uid in seen:
            return found
        seen.add(row.uid)
        current = found = row.uid


def _row_id_by_uid(session: Session, project: ProjectRef, table: str, uid: str) -> int | None:
    """``row_id`` строки ``table`` с этим uid в проекте pull'а; чужой проект — SYNC-102.

    uid склеенной сущности (CLO-023) резолвится через ``entity_alias``: операции,
    собранные до склейки, ссылаются на проигравший uid.
    """
    tbl = Base.metadata.tables[table]
    found = _row(session, table, tbl.c.uid == uid)
    if found is None:
        alias = alias_target(session, project.row_id, table, uid)
        found = None if alias is None else _row(session, table, tbl.c.uid == alias)
    if found is None:
        return None
    _require_own(session, project, table, found, f"uid {uid}")
    return int(cast("int", found[_ROW_ID]))


def _translate(
    session: Session,
    project: ProjectRef,
    info: cap.TableInfo,
    payload: Mapping[str, object],
    existing: object | None,
) -> dict[str, object]:
    """Поля операции → значения ORM-атрибутов: FK uid → row_id, время → datetime.

    Поле вне ``SHIPPED_COLUMNS`` таблицы (local, ``uid``, незнакомое) —
    SYNC-102: захват такого не пишет, значит объект собран не нашим кодом.
    """
    out: dict[str, object] = {}
    for column, value in payload.items():
        if column not in info.shipped:
            msg = f"{info.table}.{column}: колонка не уезжает — remote её не пишет"
            raise ObjectMissingError(msg)
        out[column] = _value(session, project, info, column, value, payload, existing)
    return out


def _value(
    session: Session,
    project: ProjectRef,
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
        row_id = _row_id_by_uid(session, project, target, str(value))
        if row_id is None and (column not in info.logical or column == _PROJECT_ID):
            msg = f"{info.table}.{column}: в проекте нет строки {target} с uid {value}"
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
        return _row_id_by_uid(session, project, poly_target, str(value))
    return from_wire(info.table, column, value)


# --- поиск строки -----------------------------------------------------------------


def _find(
    session: Session,
    project: ProjectRef,
    mapper: Mapper[object],
    info: cap.TableInfo,
    uid: str,
    key_payload: Mapping[str, object],
) -> object | None:
    model = mapper.class_
    if info.has_uid:
        stmt = select(model).where(getattr(model, info.attr(_UID)) == uid)
        obj = session.scalars(stmt).one_or_none()
        if obj is not None:
            _require_own(session, project, info.table, _row_of(info, obj), f"uid {uid}")
        return obj
    columns = reg.spec_for(info.table).deterministic_uid or ()
    try:
        values = _translate(session, project, info, {c: key_payload.get(c) for c in columns}, None)
    except ObjectMissingError:
        return None  # конца ребра нет в проекте — нет и ребра; создание упадёт на полях
    if any(values.get(c) is None and c not in info.nullable for c in columns):
        return None  # конец ребра не найден — и строки нет
    stmt = select(model)
    for column in columns:
        attr = getattr(model, info.attr(column))
        value = values.get(column)
        stmt = stmt.where(attr.is_(None) if value is None else attr == value)
    return session.scalars(stmt).one_or_none()


def entity_by_uid(
    session: Session, project: ProjectRef, table: str, uid: str
) -> tuple[object, cap.TableInfo] | None:
    """ORM-строка таблицы с uid в проекте (чужой проект — SYNC-102) и её ``TableInfo``.

    uid склеенной сущности резолвится через ``entity_alias``.

    Только для таблиц с колонкой ``uid``; у тег-таблиц строка адресуется
    концами ребра (``_find``).
    """
    mapper, info = _info(table)
    if not info.has_uid:
        msg = f"{table}: строка без колонки uid адресуется концами ребра"
        raise ObjectMissingError(msg)
    model = mapper.class_
    obj = session.scalars(select(model).where(getattr(model, info.attr(_UID)) == uid)).one_or_none()
    if obj is None:
        # Проигравший uid склейки (CLO-023): ревизии и ссылки, собранные до
        # склейки, указывают на него — сущность одна.
        alias = alias_target(session, project.row_id, table, uid)
        if alias is None:
            return None
        stmt = select(model).where(getattr(model, info.attr(_UID)) == alias)
        obj = session.scalars(stmt).one_or_none()
    if obj is None:
        return None
    _require_own(session, project, table, _row_of(info, obj), f"uid {uid}")
    return obj, info


def orm_values(
    session: Session,
    project: ProjectRef,
    info: cap.TableInfo,
    payload: Mapping[str, object],
    existing: object | None,
) -> dict[str, object]:
    """Поля в форме changeset'а → значения ORM-атрибутов (FK uid → row_id, время)."""
    return _translate(session, project, info, payload, existing)


# --- гейт реестра -----------------------------------------------------------------


def _require_shipped(
    session: Session,
    info: cap.TableInfo,
    obj: object,
    uid: str,
    written: Mapping[str, object] | None,
    incoming: Mapping[str, object] | None = None,
) -> None:
    """Remote не пишет строку, которую реестр этой реплики держит local.

    Тот же гейт, что у захвата (``sync_capture.shipped_fields``): restricted
    документ и всё, что на него ссылается, ``row_is_local``, придержанные
    колонки (``withheld_columns``). До записи (``written=None``) — существующая
    строка, после — итоговая с записанными полями: так не пройдёт ни правка
    local-строки, ни новая строка под restricted-документом, ни колонка, которую
    реестр для этой строки придерживает.
    """
    row = _row_of(info, obj)
    if written is None and incoming:
        # Своя нерешённая подсказка local до решения; решение remote ложится
        # поверх неё (Q-017, CLO-025) — гейт по строке уже с решением.
        decision = reg.decision_columns(info.table)
        row |= {c: v for c, v in incoming.items() if c in decision}
        if _lowers_shared_document(session, info, row, incoming):
            # Понижение отозванного документа (CLO-046): гейт — по строке уже с
            # новой меткой. Только документа, который бывал в changeset'е.
            row[reg.WITHDRAW_COLUMN] = incoming[reg.WITHDRAW_COLUMN]
    try:
        allowed = cap.shipped_fields(session, info.table, row)
    except cap.SyncCaptureError as exc:
        msg = f"{info.table} {uid}: {exc}"
        raise ObjectMissingError(msg) from exc
    if allowed is None:
        msg = f"{info.table} {uid}: строка local на этой реплике (restricted и т. п.)"
        raise ObjectMissingError(msg)
    if written is not None:
        withheld = sorted(set(written) - set(allowed))
        if withheld:
            msg = f"{info.table} {uid}: колонки {withheld} реестр придерживает"
            raise ObjectMissingError(msg)


def _lowers_shared_document(
    session: Session, info: cap.TableInfo, row: Mapping[str, object], incoming: Mapping[str, object]
) -> bool:
    """Чужая операция снимает restricted с документа, который уже бывал в changeset'е.

    Свой никогда не уезжавший restricted-документ remote не откроет: его uid в
    журнале операций реплики нет (решение 20 реестра).
    """
    column = reg.WITHDRAW_COLUMN
    if info.table != "document" or column not in incoming:
        return False
    if not reg.is_cleared(incoming[column]) or reg.is_cleared(row.get(column)):
        return False
    project_id = row.get(_PROJECT_ID)
    return isinstance(project_id, int) and cap.document_shipped(session, project_id, row.get(_UID))


def _drop_withdrawn(
    session: Session, project: ProjectRef, info: cap.TableInfo, row: Mapping[str, object], op: Op
) -> Op | None:
    """Чужая операция без того, что держит local отозванный документ (решение 20).

    ``None`` — строка целиком local из-за отозванного документа: операция
    поглощается (она в истории реплики, но не пишется). Иначе из неё вычищены
    колонки, придержанные только из-за него; ничего не осталось — тоже ``None``.
    Понижение отозванного документа проходит как есть.
    """
    if _lowers_shared_document(session, info, row, op.fields):
        return op
    held, extra = cap_withdraw.withdrawn_hold(session, project.row_id, info.table, row)
    if held:
        return None
    if not extra & op.fields.keys():
        return op
    fields = {c: v for c, v in op.fields.items() if c not in extra}
    if not fields:
        return None
    base = {c: v for c, v in op.base.items() if c not in extra}
    return dataclasses.replace(op, fields=fields, base=base)


def _finish_withdraw(
    session: Session, project: ProjectRef, info: cap.TableInfo, obj: object, op: Op
) -> list[int]:
    """Итоговая строка отзыва: свой проект, и строка перестала уезжать только меткой.

    Обычный финальный гейт (``finish_row``) отверг бы её — строка теперь local.
    Отзыв пишет одну колонку, и до записи строка уезжала (гейт до записи), так
    что remote этим путём ничего, кроме метки, в local-строку не кладёт. Тело
    документа затирается, хвост реплики по нему вычищается (решение 20).
    """
    session.flush()
    row = _row_of(info, obj)
    _require_own(session, project, info.table, row, f"{info.table} {op.entity_uid}")
    return cap_withdraw.withdraw_locally(session, project.row_id, row)


# --- каскад удаления ----------------------------------------------------------------

#: Каскадные пути от уезжающих таблиц (FK ``ON DELETE CASCADE`` и ORM-каскад
#: ``delete``), кроме ``project`` — удаление проекта changeset'ом не применяется.
#: ``gated`` — ребёнок может нести метку restricted-документа (``DOC_REFS``,
#: ``document``): строка, которую реестр держит local по метке, каскадом не
#: сносится — удаление родителя отвергается (SYNC-102). ``owned`` — ребёнок
#: принадлежит родителю и уходит с ним, даже если он local (машинный путь
#: ``affected_file``/``module_code``, метрики, derived ``link``, прогоны).
#: Полноту сверяет ``tests/services/sync/test_apply_untrusted.py``: новая
#: каскадная FK роняет тест, пока её здесь не классифицируют.
CASCADE_PATHS: Final[Mapping[tuple[str, str, str], str]] = {
    ("adr", "adr_diagram", "adr_id"): "owned",
    ("adr", "adr_relation", "from_id"): "owned",
    ("adr", "adr_relation", "to_id"): "owned",
    ("adr", "adr_supersedes", "superseded_id"): "owned",
    ("adr", "adr_supersedes", "superseding_id"): "owned",
    ("adr", "adr_task", "adr_row_id"): "owned",
    ("approval", "approval_doc_revision_link", "approval_id"): "gated",
    ("approval", "approval_task_link", "approval_id"): "owned",
    ("doc_node", "doc_node", "parent_id"): "owned",
    ("doc_node", "doc_node_suggestion", "node_id"): "owned",
    ("document", "doc_comment", "document_id"): "gated",
    ("document", "doc_node_suggestion", "document_id"): "owned",
    ("document", "document_tag", "document_id"): "gated",
    ("document", "section", "document_id"): "gated",
    ("finding", "finding_source_run", "finding_id"): "owned",
    ("module", "module_code", "module_id"): "owned",
    ("module", "module_dependency", "from_module"): "owned",
    ("module", "module_dependency", "to_module"): "owned",
    ("open_question", "open_question_link", "question_row_id"): "gated",
    ("open_question", "open_question_option", "question_row_id"): "owned",
    ("plan", "plan_section", "plan_id"): "gated",
    ("plan", "task", "plan_id"): "owned",
    ("plan_section", "task", "section_id"): "owned",
    ("routine", "routine_run", "routine_id"): "owned",
    ("scenario", "scenario_link", "scenario_row_id"): "gated",
    ("scenario", "scenario_step", "scenario_row_id"): "owned",
    ("section", "doc_comment", "section_id"): "gated",
    ("section", "link", "from_section_id"): "owned",
    ("tag", "document_tag", "tag_id"): "gated",
    ("tag", "story_tag", "tag_id"): "owned",
    ("tag", "task_tag", "tag_id"): "owned",
    ("task", "affected_file", "task_id"): "owned",
    ("task", "dependency", "from_task_id"): "owned",
    ("task", "dependency", "to_task_id"): "owned",
    ("task", "task_document", "task_id"): "owned",
    ("task", "task_metrics", "task_id"): "owned",
    ("task", "task_tag", "task_id"): "owned",
    ("task", "trace_call", "task_id"): "owned",
    ("user_story", "story_acceptance", "story_id"): "owned",
    ("user_story", "story_link", "story_id"): "gated",
    ("user_story", "story_tag", "story_id"): "owned",
}
GATED: Final = "gated"


def is_gated(child: str) -> bool:
    """Ребёнок может нести метку документа: его local-строка каскадом не сносится."""
    return child == "document" or child in reg.DOC_REFS


@functools.cache
def cascade_edges() -> frozenset[tuple[str, str, str]]:
    """Все каскадные пути схемы: (родитель, ребёнок, FK ребёнка)."""
    import cod_doc.infra.models.link_suggestions  # noqa: F401 — полная metadata

    edges: set[tuple[str, str, str]] = set()
    for name, tbl in Base.metadata.tables.items():
        for fk in tbl.foreign_keys:
            if (fk.ondelete or "").upper() == "CASCADE":
                edges.add((fk.column.table.name, name, fk.parent.name))
    for mapper in Base.registry.mappers:
        parent = cast("Table", mapper.local_table).name
        for rel in mapper.relationships:
            if rel.cascade.delete:
                child = cast("Table", rel.mapper.local_table).name
                edges.update((parent, child, column.name) for column in rel.remote_side)
    return frozenset(edges)


def _check_cascaded_row(
    session: Session, project: ProjectRef, root: str, child: str, row: Mapping[str, object]
) -> None:
    owner = row.get(_PROJECT_ID) if _PROJECT_ID in row else None
    if owner is None and child in cap.CAPTURED_TABLES:
        owner = _project_of(session, child, row)
    if owner is not None and owner != project.row_id:
        msg = f"удаление {root} снесло бы каскадом строку {child} другого проекта"
        raise ObjectMissingError(msg)
    if not is_gated(child) or child not in cap.CAPTURED_TABLES:
        return
    try:
        allowed = cap.shipped_fields(session, child, row)
    except cap.SyncCaptureError:
        allowed = None
    if allowed is None and cap_withdraw.withdrawn_hold(session, project.row_id, child, row)[0]:
        # Связь с отозванным документом (ссылка истории, тег) уходит с родителем:
        # она local только из-за отзыва, текста документа в ней нет (решение 20).
        return
    if allowed is None:
        msg = (
            f"удаление {root} снесло бы каскадом строку {child}, которую реестр этой "
            "реплики держит local (restricted)"
        )
        raise ObjectMissingError(msg)


def _check_cascade(session: Session, project: ProjectRef, table: str, row_id: int) -> None:
    """Обойти поддерево, которое снесёт удаление, до самого удаления (Q-008).

    Обход — по ``cascade_edges``, запросом на путь и пачку родителей; цикл
    (``doc_node.parent_id``) обрывает множество посещённых.
    """
    by_parent: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for parent, child, column in cascade_edges():
        by_parent[parent].append((child, column))
    seen: dict[str, set[int]] = defaultdict(set)
    seen[table].add(row_id)
    frontier: list[tuple[str, list[int]]] = [(table, [row_id])]
    while frontier:
        parent, ids = frontier.pop()
        for child, column in sorted(by_parent.get(parent, ())):
            tbl = Base.metadata.tables[child]
            found: list[int] = []
            for i in range(0, len(ids), _CHUNK):
                stmt = select(tbl).where(tbl.c[column].in_(ids[i : i + _CHUNK]))
                for raw in session.execute(stmt).mappings():
                    row = dict(raw)
                    _check_cascaded_row(session, project, table, child, row)
                    child_id = row.get(_ROW_ID)
                    if isinstance(child_id, int) and child_id not in seen[child]:
                        seen[child].add(child_id)
                        found.append(child_id)
            if found:
                frontier.append((child, found))


# --- создание ---------------------------------------------------------------------


def _fill_local(session: Session, mapper: Mapper[object], info: cap.TableInfo, obj: object) -> None:
    """``NOT NULL`` новой строки без значения: local-колонки (позиция, известные
    заглушки) и придержанные отправителем (``sync_registry.WITHHELD_FILL``)."""
    tbl = cast("Table", mapper.local_table)
    for column in tbl.columns:
        name = column.name
        if name in {"row_id", _UID} or getattr(obj, info.attr(name)) is not None:
            continue
        if name in info.shipped:
            # Уезжающая NOT NULL-колонка, которую реестр отправителя придержал
            # (ссылка на restricted-документ): заглушка вместо значения.
            if (info.table, name) in reg.WITHHELD_FILL and not column.nullable:
                setattr(obj, info.attr(name), reg.WITHHELD_FILL[(info.table, name)])
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


def _require_known_columns(info: cap.TableInfo, fields: Mapping[str, object]) -> None:
    """Поле вне ``SHIPPED_COLUMNS`` — SYNC-102, даже если слияние его отбросит."""
    for column in fields:
        if column not in info.shipped:
            msg = f"{info.table}.{column}: колонка не уезжает — remote её не пишет"
            raise ObjectMissingError(msg)


def _natural_twin(
    session: Session, mapper: Mapper[object], info: cap.TableInfo, obj: object
) -> tuple[tuple[str, ...], str] | None:
    """Своя строка другого uid с тем же натуральным ключом-именем (Q-007, CLO-023).

    Без проверки вставка упала бы ``IntegrityError`` на UNIQUE (находка F2
    секции B); арендованные номера (``leased``) не сталкиваются по построению.
    """
    if not info.has_uid:
        return None
    model = mapper.class_
    for key in reg.spec_for(info.table).natural_keys:
        if key.kind is reg.KeyKind.LEASED:
            continue
        values = [getattr(obj, info.attr(c)) for c in key.columns]
        if any(v is None for v in values):
            continue
        stmt = select(getattr(model, info.attr(_UID)))
        for column, value in zip(key.columns, values, strict=True):
            stmt = stmt.where(getattr(model, info.attr(column)) == value)
        twin = session.scalars(stmt.limit(1)).first()
        if twin is not None:
            return key.columns, str(twin)
    return None


def _apply_delete(
    session: Session, project: ProjectRef, op: Op, touched: Touched, ctx: merge.Context
) -> None:
    """Удаление идемпотентно; против правки или ссылки хвоста — стратегия ``merge``.

    Удаление выигрывает (Q-008): если поддерево задевает свой хвост, снимок
    поддерева и конфликт ``delete_edit`` пишет ``tombstone.before_delete`` — после
    проверки каскада (restricted-строку каскад не снесёт), до самого удаления.
    """
    from cod_doc.services.sync_service import tombstone

    mapper, info = _info(op.entity_kind)
    obj = _find(session, project, mapper, info, op.entity_uid, op.base)
    if obj is None:
        # Корень уже снесён своим удалением: правки этого changeset'а под ним,
        # легшие в снимок, мертвы и у его автора — конфликта нет (CLO-027).
        tombstone.drop_pending(ctx, op.entity_kind, op.entity_uid)
        return
    if cap_withdraw.withdrawn_hold(session, project.row_id, info.table, _row_of(info, obj))[0]:
        # Строку отозванного документа remote не сносит (B2): удаление поглощается.
        return
    _require_shipped(session, info, obj, op.entity_uid, None)
    if not merge.allow(session, ctx, op):
        return
    row_id = getattr(obj, _ROW_ID, None)
    if isinstance(row_id, int):
        _check_cascade(session, project, info.table, row_id)
        tombstone.before_delete(session, ctx, info.table, row_id, op)
    _note(touched, info.table, obj, deleted=True, base=op.base)
    session.delete(obj)
    # Сразу в БД: следующая операция того же uid (ребро снято и поставлено снова)
    # иначе нашла бы удаляемый объект в identity map и легла правкой в него (CLO-027).
    session.flush()
    _close_orphaned_conflicts(session, project, ctx)


def _close_orphaned_conflicts(session: Session, project: ProjectRef, ctx: merge.Context) -> None:
    """Открытые конфликты на строках, которые снесло чужое удаление, закрываются.

    Удаление выигрывает (Q-008): правка, о которой был конфликт поля или
    склейки, мертва, а если её надо вернуть — она в снимке ``delete_edit``
    реплики, сливавшей удаление со своим хвостом. Без этого конфликт
    оставался на строке, которой нет, только в тех перестановках, где правка
    пришла раньше удаления (CLO-027, сид 447). ``delete_edit`` не трогается:
    его корень удалён по определению.
    """
    from cod_doc.services import activity_service

    alive = [
        spec
        for spec in ctx.conflicts
        if spec.kind == merge.DELETE_EDIT
        or entity_by_uid(session, project, spec.entity_kind, spec.entity_uid) is not None
    ]
    if len(alive) != len(ctx.conflicts):  # ещё не записанные конфликты этого changeset'а
        ctx.conflicts = alive
        ctx.by_field = {k: v for k, v in ctx.by_field.items() if any(v is a for a in alive)}
    rows = session.scalars(
        select(SyncConflictModel).where(
            SyncConflictModel.project_id == project.row_id,
            SyncConflictModel.status == "open",
            SyncConflictModel.kind != merge.DELETE_EDIT,
        )
    ).all()
    for row in rows:
        if entity_by_uid(session, project, row.entity_kind, row.entity_uid) is not None:
            continue
        row.status = "resolved"
        row.resolved_by = "sync:deleted"
        row.resolved_at = datetime.now(UTC)
        activity_service.emit_for_write(
            session,
            project.row_id,
            "sync.conflict_resolved",
            ctx.author,
            scope_kind=row.entity_kind,
            scope_id=row.entity_uid,
            payload={"conflict_uid": row.uid, "kind": row.kind, "take": "deleted"},
            summary=f"конфликт {row.kind} {row.entity_kind} {row.entity_uid}: сущность удалена",
        )
    session.flush()


def _absorbed(session: Session, ctx: merge.Context, op: Op) -> bool:
    """Операция над потомком мёртвого родителя уходит в снимок конфликта (CLO-022)."""
    from cod_doc.services.sync_service import tombstone

    return tombstone.absorb_orphan(session, ctx, op)


def _create(
    mapper: Mapper[object],
    info: cap.TableInfo,
    uid: str | None,
    values: Mapping[str, object],
) -> object:
    obj = mapper.class_()
    if info.has_uid:
        setattr(obj, info.attr(_UID), uid)
    for column, value in values.items():
        setattr(obj, info.attr(column), value)
    return obj


def _fresh_row_id(session: Session, info: cap.TableInfo, obj: object) -> None:
    """``row_id`` новой строки — выше всех, на какие ссылаются ревизии этой реплики.

    ``row_id`` у ``section`` и соседей без AUTOINCREMENT: SQLite отдаёт новой
    строке номер удалённой, и её осиротевшие ревизии прирастают к чужой строке.
    Ревизия удалённой сущности — local (решение 19 реестра), а приросшая уезжает
    в дайджест — на одной реплике да, на другой нет. Строка, которую создаёт
    pull или восстановление снимка (CLO-022), получает номер, на который не
    ссылается ни одна ревизия.
    """
    kind = _ENTITY_KIND_OF.get(info.table)
    if kind is None:
        return
    tbl = Base.metadata.tables[info.table]
    rev = Base.metadata.tables["revision"]
    top_row = session.execute(select(func.max(tbl.c.row_id))).scalar_one_or_none() or 0
    top_rev = (
        session.execute(
            select(func.max(rev.c.entity_id)).where(rev.c.entity_kind == kind)
        ).scalar_one_or_none()
        or 0
    )
    setattr(obj, info.attr(_ROW_ID), max(int(top_row), int(top_rev)) + 1)


#: Таблица → ``EntityKind`` ревизий (адрес ревизии — ``(entity_kind, entity_id)``).
_ENTITY_KIND_OF: Final[Mapping[str, str]] = {
    table: str(kind.value) for kind, table in reg.ENTITY_KIND_TABLES.items()
}


def finish_row(
    session: Session,
    project: ProjectRef,
    info: cap.TableInfo,
    obj: object,
    uid: str,
    fields: Mapping[str, object],
) -> None:
    """Итоговая строка: проект (``project_id`` не перевешивается на чужой, ребёнок —
    под своим родителем) и гейт реестра."""
    session.flush()
    _require_own(session, project, info.table, _row_of(info, obj), f"{info.table} {uid}")
    _require_shipped(session, info, obj, uid, fields)


def create_row(
    session: Session, project: ProjectRef, table: str, uid: str | None, fields: Mapping[str, object]
) -> object:
    """Новая строка из полей в форме changeset'а — тем же путём, что чужая вставка.

    Без проверки натурального ключа: зовут её стратегии, которые сами решили,
    что строка нужна (перебуквление CLO-023, восстановление из снимка CLO-022).
    Под захватом строка даёт операцию (восстановление), под ``suspended`` — нет.
    """
    mapper, info = _info(table)
    _require_known_columns(info, fields)
    values = _translate(session, project, info, fields, None)
    obj = _create(mapper, info, uid, values)
    _fill_local(session, mapper, info, obj)
    _fresh_row_id(session, info, obj)
    session.add(obj)
    finish_row(session, project, info, obj, uid or table, fields)
    return obj


def _through_alias(session: Session, project: ProjectRef, info: cap.TableInfo, op: Op) -> Op:
    """Операция над проигравшим uid склейки (CLO-023) — над выжившим."""
    if not info.has_uid or op.entity_kind == _PROJECT:
        return op
    alias = alias_target(session, project.row_id, op.entity_kind, op.entity_uid)
    return op if alias is None else dataclasses.replace(op, entity_uid=alias)


def _apply_op(
    session: Session, project: ProjectRef, op: Op, touched: Touched, ctx: merge.Context
) -> None:
    if op.entity_kind in reg.JOURNAL_IDS:
        # Журнал — объединение по глобальному id, без слияния (§3.7, CLO-024).
        from cod_doc.services.sync_service import journal

        journal.apply_op(session, project, op)
        return
    mapper, info = _info(op.entity_kind)
    if op.entity_kind == _PROJECT and (op.entity_uid != project.uid or op.kind == "delete"):
        msg = f"операция над проектом {op.entity_uid} в changeset'е проекта {project.uid}"
        raise ObjectMissingError(msg)
    op = _through_alias(session, project, info, op)
    if op.kind == "delete":
        _apply_delete(session, project, op, touched, ctx)
        return
    _require_known_columns(info, op.fields)
    obj = _find(session, project, mapper, info, op.entity_uid, op.fields)
    if obj is not None:
        kept = _drop_withdrawn(session, project, info, _row_of(info, obj), op)
        if kept is None:
            return
        op = kept
        _require_shipped(session, info, obj, op.entity_uid, None, op.fields)
    if not merge.allow(session, ctx, op):
        return
    if obj is None and op.base and _absorbed(session, ctx, op):
        return
    if obj is not None:
        # Существующая строка: поле за полем — LWW по истории и хвосту (§3.7).
        winning = merge.fields_to_apply(session, ctx, op)
        if not winning:
            return
        op = dataclasses.replace(op, fields=winning)
    try:
        values = _translate(session, project, info, op.fields, obj)
    except ObjectMissingError:
        # Ссылка на строку, которую снёс свой tombstone: операция — в снимок.
        if obj is None and _absorbed(session, ctx, op):
            return
        raise
    if obj is None:
        final = _final_row_kept(session, project, info, None, op, values)
        if final is not None:
            _insert(session, project, mapper, info, final[0], final[1], touched, ctx)
        return
    _write_existing(session, project, info, obj, op, values, touched)


def _final_row_kept(
    session: Session,
    project: ProjectRef,
    info: cap.TableInfo,
    obj: object | None,
    op: Op,
    values: dict[str, object],
) -> tuple[Op, dict[str, object]] | None:
    """Итоговая строка под отозванным документом (новая секция, ссылка на него).

    ``None`` — операция поглощена; иначе операция и значения без колонок,
    придержанных только из-за отзыва. Сам отзыв сюда не ходит: его итоговая
    строка local по построению (``_finish_withdraw``).
    """
    if reg.is_withdraw(info.table, op.fields):
        return op, values
    row = {**(_row_of(info, obj) if obj is not None else {}), **values}
    kept = _drop_withdrawn(session, project, info, row, op)
    if kept is None:
        return None
    return kept, {c: v for c, v in values.items() if c in kept.fields}


def _write_existing(
    session: Session,
    project: ProjectRef,
    info: cap.TableInfo,
    obj: object,
    op: Op,
    values: dict[str, object],
    touched: Touched,
) -> None:
    kept = _final_row_kept(session, project, info, obj, op, values)
    if kept is None:
        return
    op, values = kept
    for column, value in values.items():
        setattr(obj, info.attr(column), value)
    if reg.is_withdraw(info.table, op.fields):
        touched.section_ids.update(_finish_withdraw(session, project, info, obj, op))
    else:
        finish_row(session, project, info, obj, op.entity_uid, op.fields)
    _note(touched, info.table, obj, deleted=False, base=op.base)


def _insert(
    session: Session,
    project: ProjectRef,
    mapper: Mapper[object],
    info: cap.TableInfo,
    op: Op,
    values: Mapping[str, object],
    touched: Touched,
    ctx: merge.Context,
) -> None:
    """Чужая вставка; совпавший натуральный ключ — стратегия ``keys`` (Q-007)."""
    if op.base:
        msg = (
            f"правка {info.table} {op.entity_uid}, которой на реплике нет: "
            "её создание старше журнала — нужен clone со снапшота (CLO-029)"
        )
        raise ObjectMissingError(msg)
    obj = _create(mapper, info, op.entity_uid, values)
    twin = _natural_twin(session, mapper, info, obj)
    if twin is not None and not merge.key_collision(session, ctx, op, *twin):
        # Стратегия склеила или перебуквила сама: затронута выжившая строка.
        target = alias_target(session, project.row_id, info.table, op.entity_uid)
        survivor = _find(session, project, mapper, info, target or op.entity_uid, op.fields)
        if survivor is not None:
            _note(touched, info.table, survivor, deleted=False, base=op.base)
        return
    _fill_local(session, mapper, info, obj)
    _fresh_row_id(session, info, obj)
    session.add(obj)
    finish_row(session, project, info, obj, op.entity_uid, op.fields)
    _note(touched, info.table, obj, deleted=False, base=op.base)


def restore_rows(
    session: Session, project: ProjectRef, rows: Iterable[Mapping[str, object]]
) -> list[object]:
    """Пересоздать строки снимка с исходными uid (resolve ``delete_edit``, CLO-022).

    ``rows`` — родитель раньше детей, поля в форме changeset'а. Строка, которая
    уже есть (её восстановили раньше или она приехала pull'ом), пропускается.
    Запись идёт под захватом: каждая строка даёт операцию, и восстановление
    уезжает следующим push'ем. Позиции и derived пересобираются, как после
    применения changeset'а.
    """
    touched = Touched()
    created: list[object] = []
    for row in rows:
        table = str(row["table"])
        uid = row.get("uid")
        fields = cast("Mapping[str, object]", row["fields"])
        mapper, info = _info(table)
        key = str(uid) if uid is not None else table
        if _find(session, project, mapper, info, key, fields) is not None:
            continue
        obj = create_row(session, project, table, None if uid is None else str(uid), fields)
        _note(touched, table, obj, deleted=False, base={})
        created.append(obj)
    _renumber(session, touched)
    _rebuild_links(session, touched.section_ids)
    _rebuild_fts(session, project, touched)
    return created


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
    # Операция над проигравшим uid склейки ложится в историю выжившего: на неё
    # смотрит LWW следующих правок этой сущности (CLO-023).
    aliases = {
        (row.entity_kind, row.alias): row.uid
        for row in session.scalars(
            select(EntityAliasModel).where(EntityAliasModel.project_id == project.row_id)
        )
    }
    rows = [
        {
            "op_id": op.op_id,
            "project_id": project.row_id,
            "replica_uid": Hlc.parse(op.hlc).replica_uid,
            "hlc": op.hlc,
            "entity_kind": op.entity_kind,
            "entity_uid": aliases.get((op.entity_kind, op.entity_uid), op.entity_uid),
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


# --- часы -----------------------------------------------------------------------


def check_clock(
    cs: Changeset, *, self_replica_uid: str, own: bool = False, now_ms: int | None = None
) -> None:
    """Штампы чужого changeset'а до первой записи: иначе remote отравит часы реплики.

    ``hlc_max`` — максимум штампов операций (это проверяет и ``Changeset``);
    реплика каждой операции — реплика changeset'а; не своя реплика (remote не
    выдаёт себя за эту машину); не дальше ``MAX_DRIFT_MS`` впереди стенных
    часов. Нарушение — SYNC-102: объект непригоден. ``own`` — свой changeset,
    узнанный ``pull._own``: его реплика обязана быть этой.
    """
    now = wall_ms() if now_ms is None else now_ms
    if (cs.replica == self_replica_uid) != own:
        msg = (
            f"changeset от реплики {cs.replica} — это uid этой реплики"
            if not own
            else f"свой changeset от чужой реплики {cs.replica}"
        )
        raise ObjectMissingError(msg)
    stamps = [Hlc.parse(op.hlc) for op in cs.ops]
    if stamps and str(max(stamps)) != cs.hlc_max:
        msg = f"hlc_max {cs.hlc_max} ≠ максимальному штампу операций {max(stamps)}"
        raise ObjectMissingError(msg)
    for op, stamp in zip(cs.ops, stamps, strict=True):
        if stamp.replica_uid != cs.replica:
            msg = f"операция {op.op_id}: реплика штампа {stamp.replica_uid} ≠ {cs.replica}"
            raise ObjectMissingError(msg)
    limit = now + MAX_DRIFT_MS
    for raw in (cs.hlc_max, *(op.hlc for op in cs.ops)):
        if Hlc.parse(raw).ms > limit:
            msg = f"штамп {raw} впереди стенных часов больше чем на {MAX_DRIFT_MS} мс"
            raise ObjectMissingError(msg)


# --- вход -------------------------------------------------------------------------


def apply_changeset(
    session: Session, project: ProjectRef, sha: str, cs: Changeset, *, ctx: merge.Context
) -> int:
    """Применить ``cs`` в транзакции ``session``; вернуть число операций.

    Сбой любой операции — исключение, и вызывающий откатывает транзакцию
    целиком: changeset применяется весь или никак. Слияние с хвостом и
    историей — ``merge`` (§3.7); конфликты, которые оно насобирало в
    ``ctx.conflicts``, открывает вызывающий в той же транзакции.
    """
    check_clock(cs, self_replica_uid=self_replica(session, project.row_id))
    touched = Touched()
    with cap.suspended(session):
        for op in cs.ops:
            _apply_op(session, project, op, touched, ctx)
        _renumber(session, touched)
        _rebuild_links(session, touched.section_ids)
        _rebuild_fts(session, project, touched)
    _record_ops(session, project, sha, cs)
    _local.record_changeset(session, project, sha, cs)
    if cs.ops:
        clock_for(session, self_replica(session, project.row_id)).observe(Hlc.parse(cs.hlc_max))
    return len(cs.ops)
