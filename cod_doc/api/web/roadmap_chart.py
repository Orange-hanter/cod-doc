"""Раскладка дорожной карты для веба (RFC 36 §3.5): координаты считает сервер.

Шаблон ``_frag/roadmap_chart.html`` только рисует: каждая полоса — ``left`` и
``width`` в процентах от шкалы, шкала — дни от ``start`` до ``end``. Шага
сборки во фронтенде нет, и разметку проверяют тесты без браузера.

Два вида одной шкалы:

- :func:`plans_chart` — экран ``/p/{slug}/roadmap``: группа на план в порядке
  очереди, внутри — задачи;
- :func:`sections_chart` — страница плана (ADO-137): группа на секцию.

Прогнозная полоса задачи — неделя до её p50 (в эту неделю очередь до неё
доходит) и светлый хвост до p85; без даты — пометка вместо полосы, не
выдуманный срок. Факт — ``created → completed_at`` (в работе — до ``asof``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from cod_doc.domain.roadmap import HistoryBar, QueueRow, Roadmap

_WEEK: Final = timedelta(days=7)
_PADDING: Final = timedelta(days=3)
_MIN_WIDTH: Final = 0.6  # %, чтобы однодневная полоса оставалась видимой
_MAX_TICKS: Final = 12
_PERCENT: Final = 100.0
_FLAG_MARKS: Final = {
    "in_progress": "▶",
    "blocked": "⛔",
    "pulled": "↑",
    "cross_plan": "⇄",
    "backlog": "·",
}
_FLAG_TITLES: Final = {
    "in_progress": "в работе",
    "blocked": "заблокирована",
    "pulled": "подтянута вперёд",
    "cross_plan": "связь с другим планом",
    "backlog": "backlog",
}
NO_DATE: Final = "без даты"


@dataclass(frozen=True, slots=True)
class Bar:
    left: float
    width: float
    kind: str  # forecast | tail | done | working
    title: str
    #: V2 (ROAD-010): источник веса у прогнозной полосы — explicit | empirical |
    #: uniform; пусто, когда очередь в задачах (источник у всех один).
    source: str = ""


@dataclass(frozen=True, slots=True)
class Row:
    task_id: str
    label: str
    href: str
    status: str
    bars: tuple[Bar, ...]
    note: str
    marks: str
    marks_title: str


@dataclass(frozen=True, slots=True)
class Group:
    key: str
    label: str
    sublabel: str
    href: str | None
    summary: tuple[Bar, ...]
    note: str
    rows: tuple[Row, ...]
    can_up: bool
    can_down: bool


@dataclass(frozen=True, slots=True)
class Tick:
    left: float
    label: str


@dataclass(frozen=True, slots=True)
class Chart:
    start: date
    end: date
    today: float
    ticks: tuple[Tick, ...]
    groups: tuple[Group, ...]
    unit: str = "tasks"


class _Scale:
    def __init__(self, start: date, end: date) -> None:
        self.start = start
        self.end = max(end, start + timedelta(days=1))
        self.days = (self.end - self.start).days

    def at(self, day: date) -> float:
        offset = (min(max(day, self.start), self.end) - self.start).days
        return round(offset / self.days * _PERCENT, 3)

    def bar(self, first: date, last: date, kind: str, title: str) -> Bar:
        left = self.at(first)
        width = max(self.at(last) - left, _MIN_WIDTH)
        return Bar(left=left, width=round(min(width, _PERCENT - left), 3), kind=kind, title=title)

    def ticks(self) -> tuple[Tick, ...]:
        monday = self.start - timedelta(days=self.start.weekday())
        weeks = math.ceil(self.days / 7) + 1
        step = max(1, math.ceil(weeks / _MAX_TICKS))
        out = []
        for i in range(0, weeks + 1, step):
            day = monday + _WEEK * i
            if self.start <= day <= self.end:
                out.append(Tick(left=self.at(day), label=day.strftime("%d.%m")))
        return tuple(out)


_SOURCE_TITLES: Final = {
    "explicit": "оценка",
    "empirical": "медиана по типу",
    "uniform": "средняя по проекту",
}


def _forecast_bars(
    scale: _Scale,
    asof: date,
    p50: date | None,
    p85: date | None,
    *,
    source: str = "",
    weight: float | None = None,
) -> tuple[Bar, ...]:
    if p50 is None:
        return ()
    title = f"≈ p50 {p50.isoformat()}" + (f", p85 {p85.isoformat()}" if p85 else "")
    if source and weight is not None:
        title += f"; вес {weight:g} чел·ч — {_SOURCE_TITLES.get(source, source)}"
    first = scale.bar(max(asof, p50 - _WEEK), p50, "forecast", title)
    bars = [Bar(first.left, first.width, first.kind, first.title, source)]
    if p85 is not None and p85 > p50:
        bars.append(scale.bar(p50, p85, "tail", title))
    return tuple(bars)


def _history_bar(scale: _Scale, bar: HistoryBar) -> Bar:
    kind = "done" if bar.status.value == "done" else "working"
    verb = "закрыта" if kind == "done" else "в работе по"
    return scale.bar(
        bar.start, bar.end, kind, f"{bar.start.isoformat()} → {verb} {bar.end.isoformat()}"
    )


def _queue_row(
    scale: _Scale,
    asof: date,
    row: QueueRow,
    p50: date | None,
    p85: date | None,
    href: str,
    *,
    hours: bool = False,
) -> Row:
    flags = sorted(flag.value for flag in row.flags)
    title = ", ".join(_FLAG_TITLES[f] for f in flags)
    if row.pulled_by:
        title += f"{'; ' if title else ''}ради {', '.join(row.pulled_by)}"
    bars = _forecast_bars(
        scale,
        asof,
        p50,
        p85,
        source=row.duration_source.value if hours else "",
        weight=row.weight if hours else None,
    )
    return Row(
        task_id=row.task_id,
        label=f"{row.task_id} {row.title}",
        href=href,
        status=row.status.value,
        bars=bars,
        note="" if bars else NO_DATE,
        marks="".join(_FLAG_MARKS[f] for f in flags),
        marks_title=title,
    )


def _done_row(scale: _Scale, bar: HistoryBar, href: str) -> Row:
    return Row(
        task_id=bar.task_id,
        label=f"{bar.task_id} {bar.title}",
        href=href,
        status=bar.status.value,
        bars=(_history_bar(scale, bar),),
        note="",
        marks="",
        marks_title="",
    )


def _domain(roadmap: Roadmap, history: Iterable[HistoryBar], since: date | None) -> _Scale:
    asof = roadmap.forecast.asof
    last = [d for r in roadmap.forecast.rows for d in (r.p50, r.p85) if d is not None]
    first = [b.start for b in history]
    start = min([asof, *first]) if since is None else min(asof, since)
    end = max([asof + _WEEK, *last])
    return _Scale(start - _PADDING, end + _PADDING)


def plans_chart(
    roadmap: Roadmap,
    *,
    task_href: Callable[[str], str],
    plan_href: Callable[[str], str | None],
    history: Sequence[HistoryBar] = (),
    since: date | None = None,
) -> Chart:
    """Экран дорожной карты: план → задачи в порядке очереди (+ факт при ``history``)."""
    scale = _domain(roadmap, history, since)
    asof = roadmap.forecast.asof
    hours = roadmap.queue.unit.value == "person_hours"
    dates = {r.task_id: r for r in roadmap.forecast.rows}
    plan_dates = {p.scope: p for p in roadmap.forecast.plans}
    done_by_plan: dict[str, list[HistoryBar]] = {}
    for bar in history:
        if bar.status.value == "done":
            done_by_plan.setdefault(bar.plan_scope, []).append(bar)
    working = {b.task_id: b for b in history if b.status.value != "done"}

    groups = []
    plans = roadmap.queue.plans
    for i, plan in enumerate(plans):
        when = plan_dates.get(plan.scope)
        summary: tuple[Bar, ...] = ()
        if when is not None and when.p50_start is not None and when.p50_finish is not None:
            title = f"≈ {when.p50_start.isoformat()} … {when.p50_finish.isoformat()}" + (
                f" (p85 {when.p85_finish.isoformat()})" if when.p85_finish else ""
            )
            summary = (
                scale.bar(max(asof, when.p50_start - _WEEK), when.p50_finish, "forecast", title),
            )
            if when.p85_finish is not None and when.p85_finish > when.p50_finish:
                summary += (scale.bar(when.p50_finish, when.p85_finish, "tail", title),)
        rows = [_done_row(scale, b, task_href(b.task_id)) for b in done_by_plan.get(plan.scope, ())]
        for row in roadmap.queue.rows:
            if row.plan_scope != plan.scope:
                continue
            d = dates[row.task_id]
            built = _queue_row(scale, asof, row, d.p50, d.p85, task_href(row.task_id), hours=hours)
            fact = working.get(row.task_id)
            if fact is not None:
                built = Row(
                    task_id=built.task_id,
                    label=built.label,
                    href=built.href,
                    status=built.status,
                    bars=(_history_bar(scale, fact), *built.bars),
                    note="",
                    marks=built.marks,
                    marks_title=built.marks_title,
                )
            rows.append(built)
        groups.append(
            Group(
                key=plan.scope,
                label=plan.scope,
                sublabel=plan.principle or "",
                href=plan_href(plan.scope),
                summary=summary,
                note="" if summary else NO_DATE,
                rows=tuple(rows),
                can_up=i > 0,
                can_down=i < len(plans) - 1,
            )
        )
    return Chart(
        start=scale.start,
        end=scale.end,
        today=scale.at(asof),
        ticks=scale.ticks(),
        groups=tuple(groups),
        unit="person_hours" if hours else "tasks",
    )


def sections_chart(
    roadmap: Roadmap | None,
    *,
    plan_scope: str,
    sections: Sequence[tuple[str, str]],
    history: Sequence[HistoryBar],
    asof: date,
    task_href: Callable[[str], str],
) -> Chart:
    """Страница плана (ADO-137): секция → задачи; факт и, если есть, прогноз.

    ``roadmap is None`` — очередь не построилась (цикл в графе): показывается
    только факт.
    """
    hours = roadmap is not None and roadmap.queue.unit.value == "person_hours"
    open_rows = [r for r in roadmap.queue.rows if r.plan_scope == plan_scope] if roadmap else []
    dates = {r.task_id: r for r in roadmap.forecast.rows} if roadmap else {}
    last = [d for r in open_rows for d in (dates[r.task_id].p50, dates[r.task_id].p85) if d]
    first = [b.start for b in history]
    scale = _Scale(min([asof, *first]) - _PADDING, max([asof + _WEEK, *last]) + _PADDING)
    working = {b.task_id: b for b in history if b.status.value != "done"}
    groups = []
    for letter, title in sections:
        rows = [
            _done_row(scale, b, task_href(b.task_id))
            for b in history
            if b.section_letter == letter and b.status.value == "done"
        ]
        for row in open_rows:
            if row.section_letter != letter:
                continue
            d = dates[row.task_id]
            built = _queue_row(scale, asof, row, d.p50, d.p85, task_href(row.task_id), hours=hours)
            fact = working.get(row.task_id)
            bars = ((_history_bar(scale, fact),) if fact else ()) + built.bars
            rows.append(
                Row(
                    task_id=built.task_id,
                    label=built.label,
                    href=built.href,
                    status=built.status,
                    bars=bars,
                    note="" if bars else NO_DATE,
                    marks=built.marks,
                    marks_title=built.marks_title,
                )
            )
        groups.append(
            Group(
                key=letter,
                label=f"{letter} · {title}",
                sublabel="",
                href=None,
                summary=(),
                note="",
                rows=tuple(rows),
                can_up=False,
                can_down=False,
            )
        )
    return Chart(
        start=scale.start,
        end=scale.end,
        today=scale.at(asof),
        ticks=scale.ticks(),
        groups=tuple(groups),
        unit="person_hours" if hours else "tasks",
    )
