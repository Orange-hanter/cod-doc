"""AFT-007 (RFC 27 F8): прогресс всех планов проекта без знания scope заранее.

Агент не мог спросить «все планы»: `plan_progress`/`plan_sections_list`
требовали `plan_scope`, а список scope добывался `SELECT scope FROM plan`.
Сервисный слой даёт список scope, проверку плана в пределах проекта с
подсказкой живых scope и прогресс по секциям всех планов фиксированным
числом запросов.

Два проекта в одной БД — изоляция hub-режима: scope уникален на БД, и
поиск без project_id нашёл бы план соседа.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event, select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

#: Сид: проект → план → секция → статусы задач (литералы, в т.ч. легаси `pending`).
_SEED: dict[str, dict[str, dict[str, list[str]]]] = {
    "alpha": {
        "plan-x": {
            "A": ["done", "done", "cancelled", "in_progress", "pending"],
            "B": ["done"],
        },
        "plan-y": {"C": ["done", "cancelled"]},
    },
    "beta": {"plan-z": {"Z": ["todo"]}},
    "gamma": {},
}


def _seed_project(session: Session, slug: str, plan_map: dict[str, dict[str, list[str]]]) -> int:
    project = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    project.created = _T0
    project.updated = _T0
    session.add(project)
    session.flush()
    counter = 0
    for i, (scope, sections) in enumerate(plan_map.items()):
        created = _T0 + timedelta(minutes=i)
        plan = PlanModel(
            project_id=project.row_id,
            scope=scope,
            principle=f"principle of {scope}",
            created=created,
            last_updated=created,
        )
        session.add(plan)
        session.flush()
        for position, (letter, statuses) in enumerate(sections.items()):
            section = PlanSectionModel(
                plan_id=plan.row_id,
                letter=letter,
                title=f"Section {letter}",
                slug=f"{letter}-Section",
                position=position,
            )
            session.add(section)
            session.flush()
            for status in statuses:
                counter += 1
                task = task_service.create(
                    session,
                    project_id=project.row_id,
                    plan_id=plan.row_id,
                    section_id=section.row_id,
                    title=f"{slug} task {counter}",
                    type=TaskType.FEATURE,
                    priority=Priority.MEDIUM,
                    author="test",
                    task_id=f"{slug.upper()}-{counter:03d}",
                )
                model = session.get(TaskModel, task.row_id)
                assert model is not None
                model.status = status
    session.flush()
    return int(project.row_id)


@pytest.fixture
def seeded(engine_with_schema: Engine) -> tuple[sessionmaker[Session], dict[str, int]]:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        ids = {slug: _seed_project(session, slug, plan_map) for slug, plan_map in _SEED.items()}
    return factory, ids


def _plan_id(session: Session, scope: str) -> int:
    return int(
        session.execute(select(PlanModel.row_id).where(PlanModel.scope == scope)).scalar_one()
    )


def test_plan_scopes_order_and_isolation(seeded: Any) -> None:
    factory, ids = seeded
    with transactional(factory) as session:
        assert plans.plan_scopes(session, ids["alpha"]) == ["plan-x", "plan-y"]
        assert plans.plan_scopes(session, ids["beta"]) == ["plan-z"]


def test_require_plan_in_project_lists_scopes(seeded: Any) -> None:
    factory, ids = seeded
    with transactional(factory) as session:
        with pytest.raises(plans.PlanNotFoundError) as missing:
            plans.require_plan_in_project(session, ids["alpha"], "nope")
        assert str(missing.value) == "Plan 'nope' not found. Available plan scopes: plan-x, plan-y"

        # План соседа неотличим от несуществующего.
        with pytest.raises(plans.PlanNotFoundError) as foreign:
            plans.require_plan_in_project(session, ids["alpha"], "plan-z")
        assert (
            str(foreign.value) == "Plan 'plan-z' not found. Available plan scopes: plan-x, plan-y"
        )

        assert plans.require_plan_in_project(session, ids["alpha"], "plan-y") == _plan_id(
            session, "plan-y"
        )


def test_require_plan_empty_project(seeded: Any) -> None:
    factory, ids = seeded
    with transactional(factory) as session, pytest.raises(plans.PlanNotFoundError) as exc:
        plans.require_plan_in_project(session, ids["gamma"], "plan-x")
    assert str(exc.value).endswith("Available plan scopes: (none)")
    assert str(exc.value) == "Plan 'plan-x' not found. Available plan scopes: (none)"


def test_section_progress_for_project_literals(seeded: Any) -> None:
    factory, ids = seeded
    with transactional(factory) as session:
        result = plans.section_progress_for_project(session, ids["alpha"])
        x_id, y_id, z_id = (_plan_id(session, s) for s in ("plan-x", "plan-y", "plan-z"))

    assert set(result) == {x_id, y_id}
    assert z_id not in result
    counters = {
        s.letter: (s.total, s.done, s.cancelled, s.in_progress)
        for sections in result.values()
        for s in sections
    }
    # `pending` — легаси-написание todo: в total, но ни в одном из счётчиков.
    assert counters == {"A": (5, 2, 1, 1), "B": (1, 1, 0, 0), "C": (2, 1, 1, 0)}
    assert "Z" not in counters


def test_section_progress_matches_recalc(seeded: Any) -> None:
    factory, ids = seeded
    with transactional(factory) as session:
        batch = plans.section_progress_for_project(session, ids["alpha"])
        for scope in ("plan-x", "plan-y"):
            plan_id = _plan_id(session, scope)
            assert batch[plan_id] == plans.recalc(session, plan_id).sections

    section_a = batch[_plan_id_from(factory, "plan-x")][0]
    assert (section_a.letter, section_a.total, section_a.done) == ("A", 5, 2)
    assert section_a.status is plans.DerivedStatus.IN_PROGRESS
    section_c = batch[_plan_id_from(factory, "plan-y")][0]
    assert (section_c.letter, section_c.status) == ("C", plans.DerivedStatus.DONE)


def _plan_id_from(factory: sessionmaker[Session], scope: str) -> int:
    with transactional(factory) as session:
        return _plan_id(session, scope)


class _StatementCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, *_args: object, **_kwargs: object) -> None:
        self.count += 1


def _count_statements(engine: Engine, factory: sessionmaker[Session], call: Any) -> int:
    counter = _StatementCounter()
    with transactional(factory) as session:
        session.connection()  # соединение и BEGIN — до подсчёта
        event.listen(engine, "before_cursor_execute", counter)
        try:
            call(session)
        finally:
            event.remove(engine, "before_cursor_execute", counter)
    return counter.count


def test_section_progress_single_query(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        one = _seed_project(session, "one", {"only": {"A": ["done"], "B": ["todo"]}})
        three = _seed_project(
            session,
            "three",
            {
                "p1": {"A": ["done"]},
                "p2": {"A": ["todo"], "B": ["in_progress"]},
                "p3": {"A": ["cancelled"], "B": ["done"], "C": ["pending"]},
            },
        )

    def sections_of(pid: int) -> Any:
        return lambda s: plans.section_progress_for_project(s, pid)

    def progress_of(pid: int) -> Any:
        return lambda s: plans.progress_for_project(s, pid, by_section=True)

    assert _count_statements(engine_with_schema, factory, sections_of(one)) == 1
    assert _count_statements(engine_with_schema, factory, sections_of(three)) == 1
    assert _count_statements(engine_with_schema, factory, progress_of(one)) == 3
    assert _count_statements(engine_with_schema, factory, progress_of(three)) == 3


def test_progress_for_project_totals_and_sections(seeded: Any) -> None:
    factory, ids = seeded
    with transactional(factory) as session:
        flat = plans.progress_for_project(session, ids["alpha"])
        deep = plans.progress_for_project(session, ids["alpha"], by_section=True)

    assert [p.scope for p in flat] == ["plan-x", "plan-y"]
    assert [(p.total, p.done, p.cancelled) for p in flat] == [(6, 3, 1), (2, 1, 1)]
    assert [p.sections for p in flat] == [[], []]

    assert [p.scope for p in deep] == ["plan-x", "plan-y"]
    assert [s.letter for s in deep[0].sections] == ["A", "B"]
    assert [s.letter for s in deep[1].sections] == ["C"]


def test_list_plans_summary_keys_and_status_filter(seeded: Any) -> None:
    factory, ids = seeded
    with transactional(factory) as session:
        rows = plans.list_plans_summary(session, ids["alpha"])
        done = plans.list_plans_summary(session, ids["alpha"], status="done")
        active = plans.list_plans_summary(session, ids["alpha"], status="in-progress")
        legacy = plans.list_plans_summary(session, ids["alpha"], status="in_progress")
        beta = plans.list_plans_summary(session, ids["beta"])
        with pytest.raises(ValueError, match="bogus") as bogus:
            plans.list_plans_summary(session, ids["alpha"], status="bogus")

    for row in rows:
        assert set(row) == {
            "scope",
            "principle",
            "status",
            "total",
            "done",
            "in_progress",
            "cancelled",
            "remaining",
        }
    assert rows[0] == {
        "scope": "plan-x",
        "principle": "principle of plan-x",
        "status": "in-progress",
        "total": 6,
        "done": 3,
        "in_progress": 1,
        "cancelled": 1,
        "remaining": 2,
    }
    assert [r["scope"] for r in done] == ["plan-y"]
    assert [r["scope"] for r in active] == ["plan-x"]
    assert [r["scope"] for r in legacy] == ["plan-x"]
    assert [r["scope"] for r in beta] == ["plan-z"]
    for value in ("empty", "pending", "in-progress", "done"):
        assert value in str(bogus.value)
