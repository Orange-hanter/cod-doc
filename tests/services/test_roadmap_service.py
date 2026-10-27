"""ROAD-003/004 (RFC 36 §3.3–3.4): очередь работ проекта и прогноз по ней.

Контракты RFC 36 §3.7: C-01, C-02 (сигнатуры), C-07 (цикл), C-08…C-10
(границы выборки), C-11 (движок не пишет), C-14 (детерминизм и монотонность),
C-15 (подтягивание блокеров), C-16 (импорты домена), C-18 (константы).

Ожидаемые порядки и даты — литералы из сида, а не вызовы кода под тестом.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.domain import roadmap as domain_roadmap
from cod_doc.domain.entities import Priority, TaskStatus
from cod_doc.domain.roadmap import (
    ForecastWarning,
    PlanBar,
    Queue,
    QueueFlag,
    QueueRow,
    ThroughputWeek,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    TaskModel,
)
from cod_doc.services import plan_service, roadmap_service
from cod_doc.services.roadmap_service import RoadmapCycleError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

AUTHOR = "agent:dakh/claude-code"
ASOF = date(2026, 10, 28)  # среда: неделя начинается 2026-10-26
ROADMAP_PY = Path(__file__).resolve().parents[2] / "cod_doc" / "domain" / "roadmap.py"


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


class _Seed:
    """Проект с планами и задачами; задачи пишутся моделью — сервис задач тут не предмет."""

    def __init__(self, session: Session, slug: str = "road") -> None:
        now = datetime.now(UTC)
        proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        self.session = session
        self.project_id = proj.row_id
        self.rows: dict[str, int] = {}
        self._sections: dict[tuple[str, str], tuple[int, int]] = {}

    def plan(self, scope: str, *letters: str) -> None:
        plan = plan_service.create_plan(
            self.session, project_id=self.project_id, scope=scope, principle=None, author=AUTHOR
        )
        for letter in letters:
            plan_service.create_section(
                self.session,
                project_id=self.project_id,
                plan_scope=scope,
                letter=letter,
                title=f"Section {letter}",
                author=AUTHOR,
            )
            section_id = self.session.scalars(
                select(PlanSectionModel.row_id).where(
                    PlanSectionModel.plan_id == plan.row_id, PlanSectionModel.letter == letter
                )
            ).one()
            assert plan.row_id is not None
            self._sections[(scope, letter)] = (plan.row_id, section_id)

    def task(
        self,
        task_id: str,
        scope: str,
        letter: str,
        *,
        status: str = "todo",
        priority: str = "medium",
        blocked_reason: str | None = None,
        created: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        plan_id, section_id = self._sections[(scope, letter)]
        stamp = created or datetime.now(UTC)
        model = TaskModel(
            project_id=self.project_id,
            task_id=task_id,
            plan_id=plan_id,
            section_id=section_id,
            title=task_id,
            status=status,
            type="feature",
            priority=priority,
            blocked_reason=blocked_reason,
            created=stamp,
            last_updated=stamp,
            completed_at=completed_at,
        )
        self.session.add(model)
        self.session.flush()
        self.rows[task_id] = model.row_id

    def blocks(self, blocker: str, blocked: str) -> None:
        """``blocker`` раньше ``blocked``: ребро (from=blocked, to=blocker)."""
        self.session.add(
            DependencyModel(
                from_task_id=self.rows[blocked], to_task_id=self.rows[blocker], kind="blocks"
            )
        )
        self.session.flush()


def _ids(queue: Queue) -> list[str]:
    return [row.task_id for row in queue.rows]


def _row(queue: Queue, task_id: str) -> QueueRow:
    return next(row for row in queue.rows if row.task_id == task_id)


# --- C-01, C-02, C-18: контракт модуля -----------------------------------------------


def test_signatures() -> None:
    assert str(inspect.signature(roadmap_service.sequence)) == (
        "(session: 'Session', *, project_id: 'int', include_backlog: 'bool' = False) -> 'Queue'"
    )
    assert list(inspect.signature(roadmap_service.throughput).parameters) == [
        "session",
        "project_id",
        "asof",
        "weeks",
    ]
    assert list(inspect.signature(roadmap_service.forecast).parameters) == [
        "queue",
        "sample",
        "asof",
        "runs",
        "seed",
    ]


def test_constants() -> None:
    assert (
        domain_roadmap.FORECAST_WINDOW_WEEKS,
        domain_roadmap.MIN_SAMPLE_WEEKS,
        domain_roadmap.HORIZON_WEEKS,
        domain_roadmap.FORECAST_RUNS,
    ) == (8, 4, 26, 1000)


def test_domain_module_imports_only_stdlib_and_domain() -> None:
    """C-16: ``domain/roadmap.py`` не тянет infra, services и SQLAlchemy."""
    tree = ast.parse(ROADMAP_PY.read_text(encoding="utf-8"))
    imported = {
        alias.name if isinstance(node, ast.Import) else (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    banned = [
        name
        for name in imported
        if name.startswith(
            (
                "cod_doc.infra",
                "cod_doc.services",
                "cod_doc.mcp",
                "cod_doc.api",
                "cod_doc.cli",
                "sqlalchemy",
            )
        )
    ]
    assert banned == []


# --- очередь ------------------------------------------------------------------------


def test_order_follows_plan_section_priority(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("first", "A", "B")
        seed.plan("second", "A")
        seed.task("S-1", "second", "A", priority="critical")
        seed.task("F-B1", "first", "B", priority="high")
        seed.task("F-A2", "first", "A", priority="low")
        seed.task("F-A1", "first", "A", priority="high")
        seed.task("F-A0", "first", "A", status="in_progress")
        seed.task("F-X", "first", "A", status="done")
        seed.task("F-Y", "first", "A", status="cancelled")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert _ids(queue) == ["F-A0", "F-A1", "F-A2", "F-B1", "S-1"]
    assert [row.position for row in queue.rows] == [0, 1, 2, 3, 4]
    assert QueueFlag.IN_PROGRESS in _row(queue, "F-A0").flags
    assert queue.plans == (
        PlanBar(
            scope="first",
            principle=None,
            rank=queue.plans[0].rank,
            first_position=0,
            last_position=3,
            open_count=4,
        ),
        PlanBar(
            scope="second",
            principle=None,
            rank=queue.plans[1].rank,
            first_position=4,
            last_position=4,
            open_count=1,
        ),
    )


def test_blocker_from_a_later_plan_is_pulled_forward(sf: sessionmaker[Session]) -> None:
    """C-15: блокер из плана №3 встаёт раньше задачи плана №1 и других задач плана №3."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.plan("p2", "A")
        seed.plan("p3", "A")
        seed.task("P1-1", "p1", "A")
        seed.task("P1-2", "p1", "A")
        seed.task("P2-1", "p2", "A")
        seed.task("P3-1", "p3", "A", priority="critical")
        seed.task("P3-2", "p3", "A", priority="low")
        seed.blocks("P3-2", "P1-2")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert _ids(queue) == ["P1-1", "P3-2", "P1-2", "P2-1", "P3-1"]
    pulled = _row(queue, "P3-2")
    assert pulled.pulled_by == ("P1-2",)
    assert {QueueFlag.PULLED, QueueFlag.CROSS_PLAN} <= pulled.flags
    assert _row(queue, "P1-1").pulled_by == ()
    assert QueueFlag.CROSS_PLAN in _row(queue, "P1-2").flags


def test_moving_a_plan_reorders_unrelated_tasks(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.plan("p2", "A")
        seed.task("P1-1", "p1", "A")
        seed.task("P2-1", "p2", "A")
        before = _ids(roadmap_service.sequence(s, project_id=seed.project_id))
        plan_service.move_plan(
            s, project_id=seed.project_id, plan_scope="p2", before="p1", author=AUTHOR
        )
        after = _ids(roadmap_service.sequence(s, project_id=seed.project_id))

    assert before == ["P1-1", "P2-1"]
    assert after == ["P2-1", "P1-1"]


def test_closed_blocker_is_satisfied(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.task("DONE", "p1", "A", status="done")
        seed.task("NEXT", "p1", "A")
        seed.blocks("DONE", "NEXT")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert _ids(queue) == ["NEXT"]
    assert _row(queue, "NEXT").flags == frozenset()


def test_cycle_across_plans_is_refused(sf: sessionmaker[Session]) -> None:
    """C-07: цикл — ошибка со списком task_id, частичной очереди нет."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.plan("p2", "A")
        seed.task("X", "p1", "A")
        seed.task("Y", "p2", "A")
        seed.task("FREE", "p1", "A")
        seed.blocks("X", "Y")
        seed.blocks("Y", "X")
        with pytest.raises(RoadmapCycleError) as err:
            roadmap_service.sequence(s, project_id=seed.project_id)

    assert [sorted(cycle) for cycle in err.value.cycles] == [["X", "Y"]]


def test_backlog_only_on_request(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.task("LATER", "p1", "A", status="backlog")
        seed.task("NOW", "p1", "A")
        seed.task("WAITS", "p1", "A")
        seed.blocks("LATER", "WAITS")
        default = roadmap_service.sequence(s, project_id=seed.project_id)
        everything = roadmap_service.sequence(s, project_id=seed.project_id, include_backlog=True)

    assert _ids(default) == ["NOW", "WAITS"]
    assert QueueFlag.BLOCKED in _row(default, "WAITS").flags
    assert _ids(everything) == ["LATER", "NOW", "WAITS"]
    assert QueueFlag.BACKLOG in _row(everything, "LATER").flags
    assert QueueFlag.BLOCKED not in _row(everything, "WAITS").flags


def test_blocked_status_and_reason_are_flagged(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.task("B1", "p1", "A", status="blocked")
        seed.task("B2", "p1", "A", blocked_reason="ждёт ключ API")
        queue = roadmap_service.sequence(s, project_id=seed.project_id)

    assert all(QueueFlag.BLOCKED in row.flags for row in queue.rows)
    assert _row(queue, "B1").status is TaskStatus.BLOCKED
    assert _row(queue, "B2").priority is Priority.MEDIUM


def test_other_projects_are_invisible(sf: sessionmaker[Session]) -> None:
    with transactional(sf) as s:
        mine = _Seed(s, "mine")
        mine.plan("p", "A")
        mine.task("MINE", "p", "A")
        theirs = _Seed(s, "theirs")
        theirs.plan("p", "A")
        theirs.task("THEIRS", "p", "A")
        queue = roadmap_service.sequence(s, project_id=mine.project_id)

    assert _ids(queue) == ["MINE"]


def test_engine_writes_nothing(sf: sessionmaker[Session]) -> None:
    """C-11: ни ревизии, ни события, ни new/dirty-объекта в сессии."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.task("T1", "p1", "A", created=datetime(2026, 10, 1, tzinfo=UTC))
        seed.task(
            "T0",
            "p1",
            "A",
            status="done",
            created=datetime(2026, 9, 1, tzinfo=UTC),
            completed_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
        s.flush()
        counts = (
            s.scalar(select(func.count(RevisionModel.row_id))),
            s.scalar(select(func.count(ActivityEventModel.row_id))),
        )
        roadmap_service.build(s, project_id=seed.project_id, asof=ASOF)
        assert not s.new
        assert not s.dirty
        assert not s.deleted
        assert (
            s.scalar(select(func.count(RevisionModel.row_id))),
            s.scalar(select(func.count(ActivityEventModel.row_id))),
        ) == counts


# --- пропускная способность -----------------------------------------------------------


def test_throughput_counts_full_weeks_only(sf: sessionmaker[Session]) -> None:
    """C-09: неделя ``asof`` (с 2026-10-26) в выборку не входит; cancelled — не закрытие."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        for i, (status, completed) in enumerate(
            (
                ("done", datetime(2026, 10, 20, 10, tzinfo=UTC)),  # неделя 10-19
                ("done", datetime(2026, 10, 25, 23, tzinfo=UTC)),  # неделя 10-19, воскресенье
                ("done", datetime(2026, 10, 13, tzinfo=UTC)),  # неделя 10-12
                ("cancelled", datetime(2026, 10, 14, tzinfo=UTC)),
                ("done", datetime(2026, 10, 26, 1, tzinfo=UTC)),  # текущая неделя — вне
            )
        ):
            seed.task(
                f"T{i}",
                "p1",
                "A",
                status=status,
                created=datetime(2026, 10, 14, tzinfo=UTC),
                completed_at=completed,
            )
        sample = roadmap_service.throughput(s, project_id=seed.project_id, asof=ASOF, weeks=3)

    assert sample == (
        ThroughputWeek(week_start=date(2026, 10, 5), closed=0, created=0),
        ThroughputWeek(week_start=date(2026, 10, 12), closed=1, created=5),
        ThroughputWeek(week_start=date(2026, 10, 19), closed=2, created=0),
    )


# --- прогноз ------------------------------------------------------------------------


def _queue(n: int) -> Queue:
    rows = tuple(
        QueueRow(
            task_id=f"T-{k:03d}",
            title="t",
            plan_scope="p" if k < n // 2 else "q",
            section_letter="A",
            position=k,
            status=TaskStatus.TODO,
            priority=Priority.MEDIUM,
            pulled_by=(),
            flags=frozenset(),
        )
        for k in range(n)
    )
    half = n // 2
    plans = (
        PlanBar("p", None, "a", 0, half - 1, half),
        PlanBar("q", None, "b", half, n - 1, n - half),
    )
    return Queue(rows=rows, plans=plans)


def _sample(*closed: int, created: int = 0) -> tuple[ThroughputWeek, ...]:
    start = date(2026, 8, 31)
    return tuple(
        ThroughputWeek(week_start=start + timedelta(weeks=i), closed=c, created=created)
        for i, c in enumerate(closed)
    )


def test_constant_throughput_gives_exact_weeks() -> None:
    result = roadmap_service.forecast(_queue(30), sample=_sample(*[10] * 8), asof=ASOF)
    row = result.rows[25]
    assert (row.p50, row.p85) == (ASOF + timedelta(weeks=3), ASOF + timedelta(weeks=3))
    assert (result.rows[0].p50, result.rows[9].p50) == (ASOF + timedelta(weeks=1),) * 2
    assert result.plans[1].p50_finish == ASOF + timedelta(weeks=3)
    assert result.warnings == ()


def test_forecast_is_deterministic_and_monotone() -> None:
    """C-14: тот же seed — тот же ответ; p85 ≥ p50; даты не убывают с позицией."""
    sample = _sample(66, 10, 14, 67, 72, 29, 12, 49)
    first = roadmap_service.forecast(_queue(150), sample=sample, asof=ASOF, seed=7)
    again = roadmap_service.forecast(_queue(150), sample=sample, asof=ASOF, seed=7)
    assert first == again
    p50 = [row.p50 for row in first.rows]
    p85 = [row.p85 for row in first.rows]
    assert all(a is not None and b is not None for a, b in zip(p50, p85, strict=True))
    assert p50 == sorted(p50)  # type: ignore[type-var]
    assert p85 == sorted(p85)  # type: ignore[type-var]
    assert all(b >= a for a, b in zip(p50, p85, strict=True))  # type: ignore[operator]


@pytest.mark.parametrize(
    ("sample", "warning"),
    [
        (_sample(10, 10, 10), ForecastWarning.SHORT_SAMPLE),
        ((), ForecastWarning.SHORT_SAMPLE),
        (_sample(0, 0, 0, 0, 0), ForecastWarning.ZERO_SAMPLE),
    ],
    ids=["three-weeks", "empty", "all-zero"],
)
def test_thin_sample_gives_order_without_dates(
    sample: tuple[ThroughputWeek, ...], warning: ForecastWarning
) -> None:
    """C-08: дат нет, предупреждение есть, исключения нет."""
    result = roadmap_service.forecast(_queue(4), sample=sample, asof=ASOF)
    assert warning in result.warnings
    assert all(row.p50 is None and row.p85 is None for row in result.rows)
    assert all(plan.p50_start is None for plan in result.plans)


def test_positions_beyond_the_horizon_have_no_dates() -> None:
    """C-10: 1 задача в неделю за 26 недель покрывает позиции 0…25, не дальше."""
    result = roadmap_service.forecast(_queue(30), sample=_sample(*[1] * 8), asof=ASOF)
    assert result.rows[25].p50 == ASOF + timedelta(weeks=26)
    assert all(row.p50 is None and row.p85 is None for row in result.rows[26:])
    assert ForecastWarning.BEYOND_HORIZON in result.warnings


def test_inflow_above_closures_is_warned() -> None:
    result = roadmap_service.forecast(_queue(4), sample=_sample(*[5] * 8, created=9), asof=ASOF)
    assert ForecastWarning.INFLOW_EXCEEDS in result.warnings


def test_empty_queue_has_no_rows() -> None:
    result = roadmap_service.forecast(Queue(rows=(), plans=()), sample=_sample(*[5] * 8), asof=ASOF)
    assert result.rows == ()
    assert result.warnings == ()


def test_history_is_facts_only(sf: sessionmaker[Session]) -> None:
    """ADO-137: факт — закрытые и в работе; todo без дат; закрытое до ``since`` — вне."""
    with transactional(sf) as s:
        seed = _Seed(s)
        seed.plan("p1", "A")
        seed.plan("p2", "A")
        seed.task(
            "OLD",
            "p1",
            "A",
            status="done",
            created=datetime(2026, 8, 1, tzinfo=UTC),
            completed_at=datetime(2026, 8, 5, tzinfo=UTC),
        )
        seed.task(
            "NEW",
            "p1",
            "A",
            status="done",
            created=datetime(2026, 10, 1, tzinfo=UTC),
            completed_at=datetime(2026, 10, 20, tzinfo=UTC),
        )
        seed.task(
            "WIP", "p1", "A", status="in_progress", created=datetime(2026, 10, 25, tzinfo=UTC)
        )
        seed.task("TODO", "p1", "A")
        seed.task(
            "OTHER",
            "p2",
            "A",
            status="done",
            created=datetime(2026, 10, 2, tzinfo=UTC),
            completed_at=datetime(2026, 10, 3, tzinfo=UTC),
        )
        plan_id = s.scalar(select(PlanModel.row_id).where(PlanModel.scope == "p1"))
        bars = roadmap_service.history(
            s, project_id=seed.project_id, asof=ASOF, since=date(2026, 9, 1), plan_id=plan_id
        )

    assert [(b.task_id, b.start, b.end) for b in bars] == [
        ("NEW", date(2026, 10, 1), date(2026, 10, 20)),
        ("WIP", date(2026, 10, 25), ASOF),
    ]
