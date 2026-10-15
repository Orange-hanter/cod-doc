"""Порядок строк по рангу — ORM-сторона ``cod_doc.infra.rank`` (RFC 29 §3.2, CLO-004).

Источник порядка — синхронизируемый ``rank`` (у ``document`` — ``node_rank``);
целый ``position`` рядом — local-производная для существующих запросов и
выдач. Отсюда три правила, которые держат все аллокаторы:

1. Чтение — ``ORDER BY rank, uid, row_id`` (``order_by``/``sort_key``): равные
   ранги, которые дают две реплики, вставившие в одно место, упорядочивает
   ``uid`` — одинаково на любой реплике.
2. Вставка и перемещение пишут ранг **одной** строки — между соседями
   (``rank_at``/``place``), ранги остальных не трогаются.
3. ``position`` пересчитывается из ранга (``renumber``) там, где он индекс в
   списке; там, где он адрес (номер критерия истории, опции вопроса,
   диаграммы ADR), — остаётся append-счётчиком, иначе поехали бы ссылки
   ``<story_id>#<position>`` и номера в CLI.

Страховка — ``before_flush``: новая строка упорядоченной таблицы без ранга
(импорт, фикстуры, путь, который ранг не ставит) получает его по своему
``position`` среди соседей — после строк с позицией не больше её, то есть как
раньше при сортировке ``(position, row_id)``. Так ``rank`` не бывает ``NULL`` у
строк, созданных через ORM.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import RelationshipDirection, Session

from cod_doc.infra import rank as _rank
from cod_doc.infra.models.base import RankMixin

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.orm import InstrumentedAttribute, UOWTransaction
    from sqlalchemy.sql.elements import ColumnElement


#: Родитель порядка у каждой таблицы с ``RankMixin`` (RFC 29 §3.2).
PARENTS: dict[str, tuple[str, ...]] = {
    "section": ("document_id",),
    "plan_section": ("plan_id",),
    "doc_node": ("project_id",),
    "scenario": ("project_id", "group_key"),
    "scenario_step": ("scenario_row_id",),
    "story_section": ("project_id",),
    "story_acceptance": ("story_id",),
    "adr_diagram": ("adr_id",),
    "adr_topic": ("project_id",),
    "open_question_option": ("question_row_id",),
}


#: Тай-брейк, которым порядок читался до 0050 (миграция бэкфиллила так же).
TIES: dict[str, str] = {"doc_node": "node_key", "story_section": "key", "scenario": "scenario_id"}


def order_by(model: type[RankMixin]) -> tuple[ColumnElement[object], ...]:
    """``ORDER BY rank, uid, row_id`` для упорядоченной модели."""
    cols: Iterable[InstrumentedAttribute[object]] = (
        getattr(model, "rank"),  # noqa: B009 — атрибуты наследника миксина
        getattr(model, "uid"),  # noqa: B009
        getattr(model, "row_id"),  # noqa: B009
    )
    return tuple(c.expression for c in cols)


def sort_key(row: RankMixin) -> tuple[str, str, int]:
    """Тот же порядок для списка в памяти (коллекция ORM, ещё не сброшенные строки)."""
    return (row.rank or "", getattr(row, "uid", None) or "", getattr(row, "row_id", None) or 0)


def rank_at(ordered: Sequence[RankMixin], index: int | None) -> str:
    """Ранг для вставки на ``index`` в упорядоченный список; ``None`` — в конец.

    Индекс зажимается в ``0…len``: ``position`` больше длины — это «в конец»,
    как было у прежних аллокаторов.
    """
    n = len(ordered)
    i = n if index is None else max(0, min(index, n))
    left = ordered[i - 1].rank if i > 0 else None
    right = ordered[i].rank if i < n else None
    return rank_between(left, right)


def rank_between(left: str | None, right: str | None) -> str:
    """``rank.between`` с ничьей: между равными рангами строки нет.

    Равные ранги соседей дают две реплики, вставившие в одно место. Новая строка
    встаёт в ту же ничью, порядок решит ``uid``: переписать ранг соседа значило
    бы нарушить правило «один ранг».
    """
    try:
        return _rank.between(left, right)
    except ValueError:
        if left is not None and right is not None and left.rstrip("0") == right.rstrip("0"):
            return left
        raise


def place[M: RankMixin](ordered: list[M], row: M, index: int | None) -> list[M]:
    """Поставить ``row`` на ``index``: один новый ранг у неё, соседи не трогаются.

    ``ordered`` может содержать саму ``row`` (перемещение) — она исключается
    до расчёта соседей. Возвращает новый порядок.
    """
    rest = [r for r in ordered if r is not row]
    i = len(rest) if index is None else max(0, min(index, len(rest)))
    row.rank = rank_at(rest, i)
    rest.insert(i, row)
    return rest


def conform(rows: Sequence[RankMixin]) -> int:
    """Привести ранги к заданному порядку ``rows``, переписав их как можно меньше.

    Для пересборок порядка целиком (экспорт сценариев, ``doc import`` с
    перестановкой секций): наибольшая возрастающая подпоследовательность рангов
    остаётся, остальные строки получают ранг между соседями. Возвращает число
    переписанных рангов; уже упорядоченный список — 0.
    """
    ranks = [r.rank for r in rows]
    keep = _longest_increasing(ranks)
    changed = 0
    for i, row in enumerate(rows):
        if i in keep:
            continue
        left = rows[i - 1].rank if i > 0 else None
        right = next((ranks[j] for j in range(i + 1, len(rows)) if j in keep), None)
        row.rank = rank_between(left, right)
        changed += 1
    return changed


def _longest_increasing(ranks: Sequence[str | None]) -> set[int]:
    """Индексы наибольшей строго возрастающей подпоследовательности (без ``None``)."""
    best: list[list[int]] = []
    for i, value in enumerate(ranks):
        chain: list[int] = []
        if value is not None:
            for j in range(i):
                prev = ranks[j]
                if prev is not None and prev < value and len(best[j]) > len(chain):
                    chain = best[j]
            chain = [*chain, i]
        best.append(chain)
    longest: list[int] = max(best, key=len) if best else []
    return set(longest)


def renumber(ordered: Sequence[object], *, start: int = 0, attr: str = "position") -> int:
    """``position = start + i`` по порядку ранга; возвращает число изменённых строк."""
    changed = 0
    for i, row in enumerate(ordered, start=start):
        if getattr(row, attr) != i:
            setattr(row, attr, i)
            changed += 1
    return changed


def siblings[M: RankMixin](session: Session, model: type[M], **parent: object) -> list[M]:
    """Строки родителя в порядке ранга (из БД, с учётом identity map)."""
    stmt = select(model).filter_by(**parent).order_by(*order_by(model))
    return list(session.execute(stmt).scalars())


def index_for_position(
    ordered: Sequence[object], position: int | None, *, tie: tuple[str, object] | None = None
) -> int:
    """Индекс новой строки, заявившей ``position``: после строк с позицией не больше.

    ``None`` — в конец. Ничья по позиции ставит новую строку после старых —
    ровно как прежняя сортировка ``(position, row_id)``. ``tie=(колонка,
    значение)`` — для таблиц, которые раньше разбивали ничью ключом
    (``doc_node.node_key``, ``story_section.key``, ``scenario.scenario_id``).
    """
    if position is None:
        return len(ordered)
    if tie is None:
        return next((i for i, r in enumerate(ordered) if _position(r) > position), len(ordered))
    column, value = tie
    claim = (position, str(value))
    return next(
        (i for i, r in enumerate(ordered) if (_position(r), str(getattr(r, column))) > claim),
        len(ordered),
    )


def insert_rank[M: RankMixin](
    session: Session,
    model: type[M],
    *,
    position: int | None = None,
    tie: tuple[str, object] | None = None,
    exclude: object = None,
    **parent: object,
) -> str:
    """Ранг строки родителя ``parent``, заявившей ``position``; соседи не меняются.

    ``exclude`` — сама строка при перемещении: её старый ранг не сосед.
    """
    rows = [r for r in siblings(session, model, **parent) if r is not exclude]
    return rank_at(rows, index_for_position(rows, position, tie=tie))


def renumber_parent[M: RankMixin](
    session: Session, model: type[M], *, start: int = 0, **parent: object
) -> list[M]:
    """Пересчитать ``position`` родителя из ранга; вернуть строки в порядке ранга."""
    rows = siblings(session, model, **parent)
    renumber(rows, start=start)
    return rows


# --- страховка: ранг новой строки без ранга ----------------------------------


def _table(model: type[object]) -> str:
    return str(getattr(model, "__tablename__"))  # noqa: B009


def _parent_value(obj: RankMixin, column: str) -> object:
    """Значение родительской колонки; FK, ещё не заполненный ORM, — объект родителя."""
    value = getattr(obj, column)
    if value is not None:
        return value
    mapper = inspect(type(obj))
    assert mapper is not None
    for rel in mapper.relationships:
        if rel.direction is not RelationshipDirection.MANYTOONE:
            continue
        if any(col.key == column for col in rel.local_columns):
            parent = getattr(obj, rel.key)
            if parent is None:
                return None
            row_id = getattr(parent, "row_id", None)
            return row_id if row_id is not None else ("pending", id(parent))
    return None


def _position(row: object) -> int:
    return int(getattr(row, "position", 0) or 0)


def _assign_missing_ranks(session: Session, _ctx: UOWTransaction, _instances: object) -> None:
    groups: dict[tuple[object, ...], list[RankMixin]] = defaultdict(list)
    for obj in session.new:
        if isinstance(obj, RankMixin):
            parent = tuple(_parent_value(obj, c) for c in PARENTS[_table(type(obj))])
            groups[(type(obj), *parent)].append(obj)
    with session.no_autoflush:
        for (model, *values), objs in groups.items():
            if any(o.rank is None for o in objs):
                assert isinstance(model, type)
                _rank_group(session, model, values, objs)


def _rank_group(
    session: Session, model: type[RankMixin], values: list[object], objs: list[RankMixin]
) -> None:
    ordered: list[RankMixin] = []
    if not any(isinstance(v, tuple) or v is None for v in values):
        parent = dict(zip(PARENTS[_table(model)], values, strict=True))
        ordered = [
            r
            for r in siblings(session, model, **parent)
            if r.rank is not None and r not in session.deleted
        ]
    ordered += [o for o in objs if o.rank is not None]
    ordered.sort(key=sort_key)
    for obj in sorted((o for o in objs if o.rank is None), key=_position):
        pos = _position(obj)
        # Перед первой строкой с позицией больше: ничья по позиции ставит новую
        # строку после старых, как прежняя сортировка ``(position, row_id)``.
        tie_col = TIES.get(_table(model))
        tie = (tie_col, getattr(obj, tie_col)) if tie_col else None
        index = index_for_position(ordered, pos, tie=tie)
        obj.rank = rank_at(ordered, index)
        ordered.insert(index, obj)


event.listen(Session, "before_flush", _assign_missing_ranks)
