"""Типы дорожной карты: очередь работ и прогноз во времени (RFC 36 §3.2.5).

Ничего из этого не хранится: значения собирает ``services/roadmap_service``
на запрос, сериализует ``services/serializers.roadmap_to_dict``, а MCP, CLI
``--json``, REST и веб отдают одну и ту же форму (контракт C-04).

Дата здесь — прогноз «когда очередь примерно дойдёт до задачи» по
пропускной способности проекта, а не срок: ни одно поле не обязательство
(RFC 36 §3.1). Модуль импортирует только stdlib и ``domain`` (контракт C-16).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from datetime import date

    from cod_doc.domain.entities import Priority, TaskStatus

#: Окно выборки пропускной способности, полных недель (контракт C-18).
FORECAST_WINDOW_WEEKS: Final = 8
#: Короче — дат нет, только порядок (C-08).
MIN_SAMPLE_WEEKS: Final = 4
#: Дальше — «за горизонтом», без дат (C-10).
HORIZON_WEEKS: Final = 26
#: Прогонов Монте-Карло.
FORECAST_RUNS: Final = 1000


class QueueFlag(StrEnum):
    """Почему строка очереди требует внимания."""

    IN_PROGRESS = "in_progress"  # в работе (или на ревью) — идёт первой
    BLOCKED = "blocked"  # status blocked, blocked_reason или блокер вне очереди
    PULLED = "pulled"  # подтянута ради задачи, стоящей раньше в порядке
    CROSS_PLAN = "cross_plan"  # есть открытое ребро в другой план
    BACKLOG = "backlog"  # только при include_backlog: ниже точки обязательства


class ForecastWarning(StrEnum):
    """Оговорки прогноза: их показывают рядом с датами, а не в документации."""

    INFLOW_EXCEEDS = "inflow_exceeds"  # за окно создано больше, чем закрыто
    SHORT_SAMPLE = "short_sample"  # недель в выборке меньше MIN_SAMPLE_WEEKS
    ZERO_SAMPLE = "zero_sample"  # ни одной закрытой задачи за окно
    BEYOND_HORIZON = "beyond_horizon"  # хвост очереди дальше HORIZON_WEEKS


@dataclass(frozen=True, slots=True)
class QueueRow:
    task_id: str
    title: str
    plan_scope: str
    section_letter: str
    position: int
    status: TaskStatus
    priority: Priority
    #: Задача, ради которой эта подтянута вперёд (пусто — стоит на своём месте).
    pulled_by: tuple[str, ...]
    flags: frozenset[QueueFlag]


@dataclass(frozen=True, slots=True)
class PlanBar:
    scope: str
    principle: str | None
    rank: str | None
    first_position: int
    last_position: int
    open_count: int


@dataclass(frozen=True, slots=True)
class Queue:
    rows: tuple[QueueRow, ...]
    plans: tuple[PlanBar, ...]


@dataclass(frozen=True, slots=True)
class ThroughputWeek:
    week_start: date
    closed: int
    created: int


@dataclass(frozen=True, slots=True)
class ForecastRow:
    task_id: str
    p50: date | None
    p85: date | None


@dataclass(frozen=True, slots=True)
class PlanForecast:
    scope: str
    p50_start: date | None
    p50_finish: date | None
    p85_finish: date | None


@dataclass(frozen=True, slots=True)
class Forecast:
    asof: date
    sample: tuple[ThroughputWeek, ...]
    rows: tuple[ForecastRow, ...]
    plans: tuple[PlanForecast, ...]
    warnings: tuple[ForecastWarning, ...]
    runs: int
    seed: int


@dataclass(frozen=True, slots=True)
class Roadmap:
    """Ответ ``roadmap_get``: очередь и прогноз по ней."""

    queue: Queue
    forecast: Forecast
