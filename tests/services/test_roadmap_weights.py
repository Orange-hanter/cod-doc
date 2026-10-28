"""ROAD-010 (RFC 36 §3.2.3, §4 V2): очередь с весами в человеко-часах.

Вес задачи — оценка → медиана ``in_progress_hours`` по типу → средняя по
проекту, с пометкой ``duration_source``; ёмкость недели — чел·ч закрытых
задач. Ни оценок, ни метрик — очередь в задачах и прогноз ровно как V1.
Ожидания — литералы, посчитанные вручную.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.roadmap import DurationSource, QueueUnit
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import TaskMetricsModel, TaskModel
from cod_doc.services import roadmap_service
from tests.services.test_roadmap_service import ASOF, _queue, _sample, _Seed

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

_DONE_AT = datetime(2026, 10, 20, tzinfo=UTC)


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _closed_with_hours(s: Session, seed: _Seed, task_id: str, task_type: str, hours: float) -> None:
    """Закрытая задача с фактом in_progress_hours (как после FLW-003)."""
    seed.task(task_id, "p1", "A", status="done", created=_DONE_AT, completed_at=_DONE_AT)
    model = s.get(TaskModel, seed.rows[task_id])
    assert model is not None
    model.type = task_type
    s.add(
        TaskMetricsModel(
            task_id=seed.rows[task_id],
            project_id=seed.project_id,
            completed_at=_DONE_AT,
            duration_hours=hours,
            in_progress_hours=hours,
            blocked_hours=None,
            revision_count=1,
            commit_count=0,
            priority="medium",
            type=task_type,
        )
    )
    s.flush()


def _open(
    s: Session, seed: _Seed, task_id: str, task_type: str, estimate: float | None = None
) -> None:
    seed.task(task_id, "p1", "A")
    model = s.get(TaskModel, seed.rows[task_id])
    assert model is not None
    model.type = task_type
    model.estimate_person_hours = estimate
    s.flush()


def test_cascade_estimate_then_type_median_then_project(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        _closed_with_hours(s, seed, "D-1", "bug", 2.0)
        _closed_with_hours(s, seed, "D-2", "bug", 4.0)
        _closed_with_hours(s, seed, "D-3", "feature", 10.0)
        _open(s, seed, "EXP", "bug", estimate=7.0)
        _open(s, seed, "EMP", "bug")
        _open(s, seed, "UNI", "docs")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert queue.unit is QueueUnit.PERSON_HOURS
    assert {r.task_id: (r.weight, r.duration_source) for r in queue.rows} == {
        "EXP": (7.0, DurationSource.EXPLICIT),
        "EMP": (3.0, DurationSource.EMPIRICAL),  # медиана bug: 2 и 4
        "UNI": (4.0, DurationSource.UNIFORM),  # медиана всех часов: 2, 4, 10
    }


def test_instant_status_flips_do_not_zero_the_medians(sf: sessionmaker[Session]) -> None:
    """ROAD-013: факт короче минуты — переход статуса скриптом, а не работа.

    ``_derive_state_durations`` округляет часы до 4 знаков, и checkout →
    complete за доли секунды пишет 0.0. На снимке orakul 161 из 169 фактов —
    0.0, медианы 0, вес каждой задачи 0, ёмкость каждой недели 0, дат нет.
    """
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        for i in range(3):
            _closed_with_hours(s, seed, f"Z-{i}", "bug", 0.0)
        _closed_with_hours(s, seed, "D-1", "bug", 2.0)
        _open(s, seed, "EMP", "bug")
        _open(s, seed, "UNI", "docs")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert queue.unit is QueueUnit.PERSON_HOURS
    assert {r.task_id: (r.weight, r.duration_source) for r in queue.rows} == {
        "EMP": (2.0, DurationSource.EMPIRICAL),
        "UNI": (2.0, DurationSource.UNIFORM),
    }


def test_only_instant_flips_keep_the_queue_in_tasks(sf: sessionmaker[Session]) -> None:
    """ROAD-013: после отсева часов нет — очередь в задачах и даты, как V1."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        _closed_with_hours(s, seed, "Z-1", "bug", 0.0)
        _closed_with_hours(s, seed, "Z-2", "feature", 0.01)
        _open(s, seed, "T-1", "bug")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)
        sample = roadmap_service.throughput(s, project_id=seed.project_id, asof=ASOF, weeks=1)

    assert queue.unit is QueueUnit.TASKS
    assert [(r.weight, r.duration_source) for r in queue.rows] == [(1.0, DurationSource.UNIFORM)]
    assert [(w.closed, w.closed_weight) for w in sample] == [(2, 2.0)]


def test_estimates_alone_switch_to_person_hours(sf: sessionmaker[Session]) -> None:
    """Метрик нет, оценка одна — средняя по проекту из оценок."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        _open(s, seed, "EXP", "feature", estimate=5.0)
        _open(s, seed, "REST", "feature")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert queue.unit is QueueUnit.PERSON_HOURS
    assert {r.task_id: (r.weight, r.duration_source) for r in queue.rows} == {
        "EXP": (5.0, DurationSource.EXPLICIT),
        "REST": (5.0, DurationSource.UNIFORM),
    }


def test_without_estimates_and_metrics_queue_stays_v1(sf: sessionmaker[Session]) -> None:
    """Регрессия: ни оценок, ни метрик — очередь в задачах, вес 1, прогноз как V1."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        for i in range(5):
            seed.task(f"T-{i}", "p1", "A")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert queue.unit is QueueUnit.TASKS
    assert {(r.weight, r.duration_source) for r in queue.rows} == {(1.0, DurationSource.UNIFORM)}
    result = roadmap_service.forecast(queue, sample=_sample(*[2] * 8), asof=ASOF, seed=5)
    weeks = [1, 1, 2, 2, 3]
    assert [(r.p50, r.p85) for r in result.rows] == [
        (ASOF + timedelta(weeks=w), ASOF + timedelta(weeks=w)) for w in weeks
    ]
    assert result.unit is QueueUnit.TASKS


def test_person_hours_forecast_consumes_weekly_hours() -> None:
    """Ёмкость 10 чел·ч/нед, веса 4, 4, 4: накоплено 4, 8, 12 → недели 1, 1, 2."""
    base = _queue(3)
    queue = replace(
        base, rows=tuple(replace(r, weight=4.0) for r in base.rows), unit=QueueUnit.PERSON_HOURS
    )
    sample = tuple(replace(w, closed_weight=10.0) for w in _sample(*[1] * 8))
    result = roadmap_service.forecast(queue, sample=sample, asof=ASOF)
    assert [r.p50 for r in result.rows] == [
        ASOF + timedelta(weeks=1),
        ASOF + timedelta(weeks=1),
        ASOF + timedelta(weeks=2),
    ]
    assert result.unit is QueueUnit.PERSON_HOURS


def test_throughput_weighs_closed_tasks_like_the_queue(sf: sessionmaker[Session]) -> None:
    """ROAD-012: закрытая задача весит медиану своего типа, а не свой факт.

    Факт ``in_progress_hours`` — wall-clock вместе с простоем; хвост в 400 ч
    раздувал ёмкость недели, а очередь расходовала её медианами — на живой БД
    все 153 открытые задачи уезжали в одну неделю. Медиана bug (1, 2, 400) =
    2.0, три закрытия — 6.0 против факта 403.
    """
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        _closed_with_hours(s, seed, "D-1", "bug", 1.0)
        _closed_with_hours(s, seed, "D-2", "bug", 2.0)
        _closed_with_hours(s, seed, "D-3", "bug", 400.0)
        sample = roadmap_service.throughput(s, project_id=seed.project_id, asof=ASOF, weeks=2)

    assert [(w.week_start, w.closed, w.closed_weight) for w in sample] == [
        (date(2026, 10, 12), 0, 0.0),
        (date(2026, 10, 19), 3, 6.0),
    ]


def test_throughput_uses_estimate_of_closed_task(sf: sessionmaker[Session]) -> None:
    """Оценка закрытой задачи бьёт и эмпирику, и её собственный факт — как в очереди."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        _closed_with_hours(s, seed, "D-1", "bug", 1.0)
        _closed_with_hours(s, seed, "D-2", "bug", 30.0)
        model = s.get(TaskModel, seed.rows["D-2"])
        assert model is not None
        model.estimate_person_hours = 5.0
        s.flush()
        sample = roadmap_service.throughput(s, project_id=seed.project_id, asof=ASOF, weeks=1)

    # D-1 — медиана bug (1, 30) = 15.5; D-2 — оценка 5.0.
    assert [w.closed_weight for w in sample] == [20.5]
