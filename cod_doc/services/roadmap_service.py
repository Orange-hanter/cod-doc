"""Дорожная карта: очередь работ проекта и прогноз во времени (RFC 36 §3.3–3.4).

Три слоя (§3.1): факт (``created``/``completed_at``) и порядок (ранг плана,
ранг секции, приоритет, рёбра ``blocks``) читаются из БД, прогноз —
вычисляется здесь и **нигде не хранится**. Модуль только читает: ни ревизий,
ни событий, ни записи в сессию (контракт C-11).

Два вопроса, которые не путать:

- ``plan_service.graph.critical_path`` — самая длинная цепочка зависимостей
  **внутри одного плана**, по числу задач;
- :func:`sequence` — порядок работ **всего проекта**, включая рёбра между
  планами; в UI это «очередь».

Дата прогноза — «когда очередь примерно дойдёт до задачи» по недельной
пропускной способности, вилкой p50/p85. Это не срок (RFC 36 §1).
"""

from __future__ import annotations

import heapq
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskStatus, canonical_task_status
from cod_doc.domain.roadmap import (
    EMPIRICAL_WINDOW_DAYS,
    FORECAST_RUNS,
    FORECAST_WINDOW_WEEKS,
    HORIZON_WEEKS,
    MIN_EMPIRICAL_HOURS,
    MIN_SAMPLE_WEEKS,
    DurationSource,
    Forecast,
    ForecastRow,
    ForecastWarning,
    HistoryBar,
    PlanBar,
    PlanForecast,
    Queue,
    QueueFlag,
    QueueRow,
    QueueUnit,
    Roadmap,
    ThroughputWeek,
)
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    TaskMetricsModel,
    TaskModel,
)
from cod_doc.services.plan_service.audit import _find_cycles

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.orm import Session

__all__ = ["RoadmapCycleError", "build", "forecast", "history", "sequence", "throughput"]

_DAYS_PER_WEEK: Final = 7
_P50: Final = 0.5
_P85: Final = 0.85
_CLOSED: Final = frozenset({TaskStatus.DONE.value, TaskStatus.CANCELLED.value})
_WORKING: Final = frozenset({TaskStatus.IN_PROGRESS_NEW.value, TaskStatus.IN_REVIEW.value})
_PRIORITY_ORDER: Final = {p.value: i for i, p in enumerate(Priority)}
_NO_RANK: Final = "~"  # после любого ранга: строка без ранга не обгоняет ранжированные
_REACHED_EPS: Final = 1e-9  # сумма float-весов: позиция покрыта, если вес ≤ ёмкости с допуском


class RoadmapCycleError(ValueError):
    """В графе проекта цикл: очереди нет, а не «частичная очередь» (контракт C-07)."""

    def __init__(self, cycles: list[list[str]]) -> None:
        self.cycles = cycles
        shown = "; ".join(" → ".join(cycle) for cycle in cycles)
        super().__init__(f"dependency cycle in the project graph: {shown}")


# --- очередь ------------------------------------------------------------------------


@dataclass(slots=True)
class _Node:
    row_id: int
    task_id: str
    title: str
    status: str
    priority: str
    blocked_reason: str | None
    plan_id: int
    plan_scope: str
    plan_principle: str | None
    plan_rank: str | None
    plan_key: tuple[str, str, int]
    section_letter: str
    section_key: tuple[str, int]
    type: str
    estimate: float | None


_Key = tuple[int, tuple[str, str, int], tuple[str, int], int, str]


def _key(node: _Node) -> _Key:
    """Ключ порядка: в работе — первыми, затем план, секция, приоритет, task_id."""
    return (
        0 if node.status in _WORKING else 1,
        node.plan_key,
        node.section_key,
        _PRIORITY_ORDER.get(node.priority, len(_PRIORITY_ORDER)),
        node.task_id,
    )


def _load_nodes(session: Session, project_id: int, *, include_backlog: bool) -> list[_Node]:
    rows = session.execute(
        select(
            TaskModel.row_id,
            TaskModel.task_id,
            TaskModel.title,
            TaskModel.status,
            TaskModel.priority,
            TaskModel.blocked_reason,
            PlanModel.row_id,
            PlanModel.scope,
            PlanModel.principle,
            PlanModel.rank,
            PlanModel.uid,
            PlanSectionModel.letter,
            PlanSectionModel.rank,
            PlanSectionModel.row_id,
            TaskModel.type,
            TaskModel.estimate_person_hours,
        )
        .join(PlanModel, PlanModel.row_id == TaskModel.plan_id)
        .join(PlanSectionModel, PlanSectionModel.row_id == TaskModel.section_id)
        .where(TaskModel.project_id == project_id)
    ).all()
    nodes: list[_Node] = []
    for (
        row_id,
        task_id,
        title,
        raw_status,
        priority,
        blocked_reason,
        plan_id,
        scope,
        principle,
        plan_rank,
        plan_uid,
        letter,
        section_rank,
        section_row_id,
        task_type,
        estimate,
    ) in rows:
        status = canonical_task_status(raw_status)
        if status in _CLOSED or (status == TaskStatus.BACKLOG.value and not include_backlog):
            continue
        nodes.append(
            _Node(
                row_id=row_id,
                task_id=task_id,
                title=title,
                status=status,
                priority=priority,
                blocked_reason=blocked_reason,
                plan_id=plan_id,
                plan_scope=scope,
                plan_principle=principle,
                plan_rank=plan_rank,
                plan_key=(plan_rank or _NO_RANK, plan_uid or "", plan_id),
                section_letter=letter,
                section_key=(section_rank or _NO_RANK, section_row_id),
                type=task_type,
                estimate=estimate,
            )
        )
    return nodes


# --- веса (V2, ROAD-010) ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _WeightModel:
    """Как взвешивать задачи: единица очереди, медианы по типу, средняя по проекту."""

    unit: QueueUnit
    by_type: dict[str, float]
    fallback: float


_TASKS_MODEL: Final = _WeightModel(QueueUnit.TASKS, {}, 1.0)


def _weight_model(session: Session, project_id: int) -> _WeightModel:
    """Единица очереди и эмпирика (RFC 36 §4 V2).

    Очередь в чел·ч, если в проекте есть хоть какие-то часы: эмпирика
    (``task_metrics.in_progress_hours`` за ``EMPIRICAL_WINDOW_DAYS`` до самой
    свежей строки метрик — окно привязано к данным, не к часам) или оценки
    задач. Нет ни того ни другого — очередь в задачах, ровно как V1. Средняя
    по проекту — медиана всех известных часов: эмпирики, иначе оценок.

    Факт короче ``MIN_EMPIRICAL_HOURS`` в эмпирику не идёт (ROAD-013): это
    переход статуса скриптом или агентом за доли секунды, округлённый до 0.0.
    На orakul такие — 161 из 169 фактов; с ними медианы были 0, вес каждой
    задачи и ёмкость каждой недели — 0, и прогноз не давал ни одной даты.
    """
    real_hours = TaskMetricsModel.in_progress_hours >= MIN_EMPIRICAL_HOURS  # NULL отсекает тоже
    newest = session.scalar(
        select(TaskMetricsModel.completed_at)
        .where(TaskMetricsModel.project_id == project_id, real_hours)
        .order_by(TaskMetricsModel.completed_at.desc())
        .limit(1)
    )
    samples: dict[str, list[float]] = defaultdict(list)
    if newest is not None:
        since = newest - timedelta(days=EMPIRICAL_WINDOW_DAYS)
        for task_type, hours in session.execute(
            select(TaskMetricsModel.type, TaskMetricsModel.in_progress_hours).where(
                TaskMetricsModel.project_id == project_id,
                real_hours,
                TaskMetricsModel.completed_at >= since,
            )
        ).all():
            if hours is not None:  # отсечено и в SQL; тип колонки всё равно nullable
                samples[task_type].append(float(hours))
    estimates = [
        float(h)
        for (h,) in session.execute(
            select(TaskModel.estimate_person_hours).where(
                TaskModel.project_id == project_id,
                TaskModel.estimate_person_hours.is_not(None),
            )
        ).all()
        if h is not None
    ]
    empirical = [h for values in samples.values() for h in values]
    known = empirical or estimates
    if not known:
        return _TASKS_MODEL
    return _WeightModel(
        unit=QueueUnit.PERSON_HOURS,
        by_type={t: statistics.median(v) for t, v in samples.items()},
        fallback=statistics.median(known),
    )


def _weight(
    model: _WeightModel, task_type: str, estimate: float | None
) -> tuple[float, DurationSource]:
    """Вес задачи: оценка → медиана по типу → средняя по проекту (контракт ROAD-010)."""
    if model.unit is QueueUnit.TASKS:
        return 1.0, DurationSource.UNIFORM
    if estimate is not None:
        return float(estimate), DurationSource.EXPLICIT
    if task_type in model.by_type:
        return model.by_type[task_type], DurationSource.EMPIRICAL
    return model.fallback, DurationSource.UNIFORM


def _load_edges(session: Session, row_ids: Iterable[int]) -> list[tuple[int, int, str]]:
    """Рёбра ``blocks`` у открытых задач: (блокер, заблокированная, статус блокера).

    ``(from=B, to=A)`` значит «A раньше B»: блокер — ``to_task_id``.
    """
    ids = list(row_ids)
    if not ids:
        return []
    rows = session.execute(
        select(DependencyModel.to_task_id, DependencyModel.from_task_id, TaskModel.status)
        .join(TaskModel, TaskModel.row_id == DependencyModel.to_task_id)
        .where(DependencyModel.kind == "blocks", DependencyModel.from_task_id.in_(ids))
    ).all()
    return [(int(a), int(b), canonical_task_status(s)) for a, b, s in rows]


def _effective_keys(
    nodes: dict[int, _Node], dependents: dict[int, list[int]], order: list[int]
) -> tuple[dict[int, _Key], dict[int, int]]:
    """Подтягивание блокеров: ключ задачи — минимум по ней и всем, кого она держит.

    ``order`` — топологический порядок (блокеры раньше), обходится с конца, так
    что ключи зависимых готовы к моменту блокера. Второй словарь — откуда
    взялся минимум (row_id задачи-источника).
    """
    effective: dict[int, _Key] = {}
    origin: dict[int, int] = {}
    for row_id in reversed(order):
        best, source = _key(nodes[row_id]), row_id
        for child in dependents.get(row_id, ()):
            if effective[child] < best:
                best, source = effective[child], origin[child]
        effective[row_id], origin[row_id] = best, source
    return effective, origin


def _topological(nodes: Iterable[int], dependents: dict[int, list[int]]) -> list[int]:
    indegree: dict[int, int] = dict.fromkeys(nodes, 0)
    for children in dependents.values():
        for child in children:
            indegree[child] += 1
    ready = sorted(n for n, d in indegree.items() if d == 0)
    out: list[int] = []
    while ready:
        node = ready.pop()
        out.append(node)
        for child in dependents.get(node, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return out


def sequence(session: Session, *, project_id: int, include_backlog: bool = False) -> Queue:
    """Очередь работ всего проекта (RFC 36 §3.3; контракты C-01, C-07, C-11, C-15).

    Открытые задачи всех планов и рёбра ``blocks`` между ними, включая
    межплановые; ребро на закрытую задачу выполнено. Порядок — алгоритм Кана
    с кучей по эффективному ключу: блокер встаёт раньше каждой задачи, которую
    держит, даже из плана с меньшим рангом, и несёт ``pulled_by``. Цикл —
    ``RoadmapCycleError`` со всеми циклами, частичной очереди нет.
    """
    nodes = {n.row_id: n for n in _load_nodes(session, project_id, include_backlog=include_backlog)}
    dependents: dict[int, list[int]] = defaultdict(list)
    outside_blocker: set[int] = set()
    cross_plan: set[int] = set()
    for blocker, blocked, blocker_status in _load_edges(session, nodes):
        if blocked not in nodes or blocker_status in _CLOSED:
            continue
        if blocker not in nodes:  # держит задача вне очереди (backlog без флага)
            outside_blocker.add(blocked)
            continue
        dependents[blocker].append(blocked)
        if nodes[blocker].plan_id != nodes[blocked].plan_id:
            cross_plan.update((blocker, blocked))

    cycles = _find_cycles({n: list(dependents.get(n, ())) for n in nodes})
    if cycles:
        raise RoadmapCycleError([[nodes[r].task_id for r in cycle] for cycle in cycles])

    order = _topological(nodes, dependents)
    effective, origin = _effective_keys(nodes, dependents, order)
    weights = _weight_model(session, project_id)

    indegree: dict[int, int] = dict.fromkeys(nodes, 0)
    for children in dependents.values():
        for child in children:
            indegree[child] += 1
    heap = [(effective[n], n) for n, d in indegree.items() if d == 0]
    heapq.heapify(heap)
    rows: list[QueueRow] = []
    while heap:
        _, row_id = heapq.heappop(heap)
        node = nodes[row_id]
        source = origin[row_id]
        weight, duration_source = _weight(weights, node.type, node.estimate)
        flags = {
            flag
            for flag, on in (
                (QueueFlag.IN_PROGRESS, node.status in _WORKING),
                (
                    QueueFlag.BLOCKED,
                    node.status == TaskStatus.BLOCKED.value
                    or bool(node.blocked_reason)
                    or row_id in outside_blocker,
                ),
                (QueueFlag.PULLED, source != row_id),
                (QueueFlag.CROSS_PLAN, row_id in cross_plan),
                (QueueFlag.BACKLOG, node.status == TaskStatus.BACKLOG.value),
            )
            if on
        }
        rows.append(
            QueueRow(
                task_id=node.task_id,
                title=node.title,
                plan_scope=node.plan_scope,
                section_letter=node.section_letter,
                position=len(rows),
                status=TaskStatus(node.status),
                priority=Priority(node.priority),
                pulled_by=(nodes[source].task_id,) if source != row_id else (),
                flags=frozenset(flags),
                weight=weight,
                duration_source=duration_source,
            )
        )
        for child in dependents.get(row_id, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(heap, (effective[child], child))

    return Queue(rows=tuple(rows), plans=_plan_bars(nodes, rows), unit=weights.unit)


def _plan_bars(nodes: dict[int, _Node], rows: Sequence[QueueRow]) -> tuple[PlanBar, ...]:
    by_scope: dict[str, _Node] = {n.plan_scope: n for n in nodes.values()}
    positions: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        positions[row.plan_scope].append(row.position)
    bars = [
        PlanBar(
            scope=scope,
            principle=by_scope[scope].plan_principle,
            rank=by_scope[scope].plan_rank,
            first_position=min(found),
            last_position=max(found),
            open_count=len(found),
        )
        for scope, found in positions.items()
    ]
    return tuple(sorted(bars, key=lambda b: by_scope[b.scope].plan_key))


# --- пропускная способность ----------------------------------------------------------


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _as_date(value: datetime) -> date:
    return (value.astimezone(UTC) if value.tzinfo else value).date()


def throughput(
    session: Session,
    *,
    project_id: int,
    asof: date,
    weeks: int = FORECAST_WINDOW_WEEKS,
) -> tuple[ThroughputWeek, ...]:
    """Закрытые (``done``) и созданные задачи по полным неделям до недели ``asof``.

    Неделя — с понедельника. Текущая, неполная неделя в выборку не входит
    (контракт C-09): на снимке 2026-10-19 её единственный день дал бы 8
    закрытых против медианы 39 и утащил прогноз.

    V2 (ROAD-010): ``closed_weight`` — те же закрытия в чел·ч, взвешенные
    тем же каскадом, что очередь: оценка, эмпирика по типу, средняя по
    проекту. Факт ``in_progress_hours`` задачи в её вес не идёт (ROAD-012):
    это wall-clock вместе с простоем, хвост в сотни часов раздувал ёмкость
    недели, а очередь расходовала её медианами — на живой БД 153 открытые
    задачи укладывались в одну неделю. Факт входит только в медианы. Очередь
    в задачах — ``closed_weight`` равен ``closed``.
    """
    end = _week_start(asof)
    start = end - timedelta(days=_DAYS_PER_WEEK * weeks)
    since = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    model = _weight_model(session, project_id)
    closed: dict[date, int] = defaultdict(int)
    closed_weight: dict[date, float] = defaultdict(float)
    created: dict[date, int] = defaultdict(int)
    for status, completed_at, task_type, estimate in session.execute(
        select(
            TaskModel.status,
            TaskModel.completed_at,
            TaskModel.type,
            TaskModel.estimate_person_hours,
        ).where(TaskModel.project_id == project_id, TaskModel.completed_at >= since)
    ).all():
        if completed_at is not None and canonical_task_status(status) == TaskStatus.DONE.value:
            week = _week_start(_as_date(completed_at))
            closed[week] += 1
            closed_weight[week] += _weight(model, task_type, estimate)[0]
    for (created_at,) in session.execute(
        select(TaskModel.created).where(
            TaskModel.project_id == project_id, TaskModel.created >= since
        )
    ).all():
        created[_week_start(_as_date(created_at))] += 1
    out = []
    for i in range(weeks):
        week = start + timedelta(days=_DAYS_PER_WEEK * i)
        out.append(
            ThroughputWeek(
                week_start=week,
                closed=closed[week],
                created=created[week],
                closed_weight=round(closed_weight[week], 4),
            )
        )
    return tuple(out)


# --- прогноз ------------------------------------------------------------------------


def _weeks_to_reach(
    cumulative: Sequence[float], capacity: Sequence[float], rng: random.Random
) -> list[int | None]:
    """Один прогон: позиция k покрыта в неделю, когда ёмкость дошла до её накопленного веса.

    ``cumulative[k]`` — сумма весов позиций 0…k. С весом 1 и ёмкостью «закрыто
    задач» это прежний V1: та же проверка перед броском, те же броски ``rng``.
    """
    n = len(cumulative)
    reached: list[int | None] = [None] * n
    done = 0.0
    k = 0
    for week in range(1, HORIZON_WEEKS + 1):
        if k >= n:
            break
        done += rng.choice(capacity)
        while k < n and cumulative[k] <= done + _REACHED_EPS:
            reached[k] = week
            k += 1
    return reached


def _quantile(values: list[int | None], q: float) -> int | None:
    """Порядковая статистика; ``None`` (за горизонтом) — больше любой недели."""
    ranked = sorted(values, key=lambda v: math.inf if v is None else v)
    return ranked[max(0, math.ceil(q * len(ranked)) - 1)]


def forecast(
    queue: Queue,
    *,
    sample: Sequence[ThroughputWeek],
    asof: date,
    runs: int = FORECAST_RUNS,
    seed: int = 0,
) -> Forecast:
    """Прогноз по выборке недель — Монте-Карло, вилка p50/p85 (RFC 36 §3.4).

    Чистая функция: ни сессии, ни ``now()`` (контракт C-14 — одинаковые
    ``queue``, ``sample``, ``asof``, ``seed`` дают одинаковый результат).
    Позиция, которую за ``HORIZON_WEEKS`` не покрывает и половина прогонов,
    получает ``p50 = p85 = None`` (C-10); выборка короче ``MIN_SAMPLE_WEEKS``
    или из одних нулей — даты не считаются вовсе (C-08).
    """
    warnings: list[ForecastWarning] = []
    closed = [w.closed for w in sample]
    if sum(w.created for w in sample) > sum(closed):
        warnings.append(ForecastWarning.INFLOW_EXCEEDS)
    # V2: очередь в чел·ч расходует ёмкость в чел·ч; в задачах — как V1.
    hours = queue.unit is QueueUnit.PERSON_HOURS
    capacity = [w.closed_weight for w in sample] if hours else [float(c) for c in closed]
    n = len(queue.rows)
    p50: list[int | None] = [None] * n
    p85: list[int | None] = [None] * n
    if len(sample) < MIN_SAMPLE_WEEKS:
        warnings.append(ForecastWarning.SHORT_SAMPLE)
    elif not any(capacity):
        warnings.append(ForecastWarning.ZERO_SAMPLE)
    elif n:
        rng = random.Random(seed)
        cumulative: list[float] = []
        total = 0.0
        for row in queue.rows:
            total += row.weight if hours else 1.0
            cumulative.append(total)
        per_position: list[list[int | None]] = [[] for _ in range(n)]
        for _ in range(runs):
            for k, week in enumerate(_weeks_to_reach(cumulative, capacity, rng)):
                per_position[k].append(week)
        p50 = [_quantile(v, _P50) for v in per_position]
        p85 = [_quantile(v, _P85) for v in per_position]
        if any(w is None for w in p85):
            warnings.append(ForecastWarning.BEYOND_HORIZON)

    def at(week: int | None) -> date | None:
        return None if week is None else asof + timedelta(days=_DAYS_PER_WEEK * week)

    rows = tuple(
        ForecastRow(task_id=row.task_id, p50=at(p50[row.position]), p85=at(p85[row.position]))
        for row in queue.rows
    )
    plans = tuple(
        PlanForecast(
            scope=bar.scope,
            p50_start=at(p50[bar.first_position]),
            p50_finish=at(p50[bar.last_position]),
            p85_finish=at(p85[bar.last_position]),
        )
        for bar in queue.plans
    )
    return Forecast(
        asof=asof,
        sample=tuple(sample),
        rows=rows,
        plans=plans,
        warnings=tuple(warnings),
        runs=runs,
        seed=seed,
        unit=queue.unit,
    )


def history(
    session: Session,
    *,
    project_id: int,
    asof: date,
    since: date | None = None,
    plan_id: int | None = None,
) -> tuple[HistoryBar, ...]:
    """Факт для тумблера «история» (RFC 36 §3.5, ADO-137): слой «факт», без прогноза.

    Закрытые (``done``) задачи — от ``created`` до ``completed_at``; задачи в
    работе (``in_progress``/``in_review``) — от ``created`` до ``asof``.
    ``since`` отсекает задачи, закрытые раньше; у ``todo`` дат факта нет, и
    фиктивных им не ставим. Порядок — по началу, затем ``task_id``.
    """
    stmt = (
        select(
            TaskModel.task_id,
            TaskModel.title,
            TaskModel.status,
            TaskModel.created,
            TaskModel.completed_at,
            PlanModel.scope,
            PlanSectionModel.letter,
        )
        .join(PlanModel, PlanModel.row_id == TaskModel.plan_id)
        .join(PlanSectionModel, PlanSectionModel.row_id == TaskModel.section_id)
        .where(TaskModel.project_id == project_id)
    )
    if plan_id is not None:
        stmt = stmt.where(TaskModel.plan_id == plan_id)
    bars: list[HistoryBar] = []
    for task_id, title, raw_status, created, completed_at, scope, letter in session.execute(
        stmt
    ).all():
        status = canonical_task_status(raw_status)
        if status == TaskStatus.DONE.value and completed_at is not None:
            end = _as_date(completed_at)
        elif status in _WORKING:
            end = asof
        else:
            continue
        if since is not None and end < since:
            continue
        bars.append(
            HistoryBar(
                task_id=task_id,
                title=title,
                plan_scope=scope,
                section_letter=letter,
                status=TaskStatus(status),
                start=min(_as_date(created), end),
                end=end,
            )
        )
    return tuple(sorted(bars, key=lambda b: (b.start, b.task_id)))


def build(
    session: Session,
    *,
    project_id: int,
    asof: date,
    include_backlog: bool = False,
    weeks: int = FORECAST_WINDOW_WEEKS,
    seed: int = 0,
) -> Roadmap:
    """Очередь + прогноз — то, что отдают MCP, CLI, REST и веб (контракт C-04)."""
    queue = sequence(session, project_id=project_id, include_backlog=include_backlog)
    sample = throughput(session, project_id=project_id, asof=asof, weeks=weeks)
    return Roadmap(queue=queue, forecast=forecast(queue, sample=sample, asof=asof, seed=seed))
