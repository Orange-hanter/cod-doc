"""AFT-011: выделение task_id — префикс из плана, занятый ID, ретрай авто-ID."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select

from cod_doc.domain.entities import Priority, Task, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.infra.repositories import TaskRepository
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _seed_plan(session: Session, project_id: int, scope: str) -> tuple[int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope=scope, created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id


def _create(
    session: Session, project_id: int, plan_id: int, section_id: int, title: str, **kw: Any
) -> Task:
    return task_service.create(
        session,
        project_id=project_id,
        plan_id=plan_id,
        section_id=section_id,
        title=title,
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        **kw,
    )


def _count_tasks(session: Session, project_id: int) -> int:
    return session.execute(
        select(func.count()).select_from(TaskModel).where(TaskModel.project_id == project_id)
    ).scalar_one()


def test_prefix_from_existing_plan_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        pl, s = _seed_plan(session, p, "adoption-2026-08")
        _create(session, p, pl, s, "a", task_id="ADO-004")
        _create(session, p, pl, s, "b", task_id="ADO-005")

        task = _create(session, p, pl, s, "c")
        assert task.task_id == "ADO-006"


def test_existing_tasks_beat_scope(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        pl, s = _seed_plan(session, p, "agent-fit")
        _create(session, p, pl, s, "a", task_id="AFT-001")
        _create(session, p, pl, s, "b", task_id="AFT-002")

        task = _create(session, p, pl, s, "c")
        assert task.task_id == "AFT-003"


def test_empty_plan_uses_scope(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        pl1, s1 = _seed_plan(session, p, "adoption-2026-08")
        pl2, s2 = _seed_plan(session, p, "x-1")

        assert _create(session, p, pl1, s1, "a").task_id == "ADO-001"
        assert _create(session, p, pl2, s2, "b").task_id == "TSK-001"


def test_id_prefix_from_scope_literals() -> None:
    assert task_service.id_prefix_from_scope("adoption-2026-08") == "ADO"
    assert task_service.id_prefix_from_scope("cod-doc") == "COD"
    assert task_service.id_prefix_from_scope("x") == "TSK"
    assert task_service.id_prefix_from_scope("2026") == "TSK"


def test_explicit_taken_id_raises_structured(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        pl, s = _seed_plan(session, p, "adoption-2026-08")
        _create(session, p, pl, s, "a", task_id="ADO-001")
        _create(session, p, pl, s, "b", task_id="ADO-007")

        with pytest.raises(task_service.DuplicateTaskIdError) as info:
            _create(session, p, pl, s, "c", task_id="ADO-001")

    exc = info.value
    assert exc.task_id == "ADO-001"
    assert exc.next_free_id == "ADO-008"
    text = str(exc)
    assert "INSERT" not in text
    assert "UNIQUE constraint" not in text
    assert "sqlite3" not in text


def test_auto_id_retries_on_injected_conflict(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Критерий 4: параллельный create занял номер между расчётом и insert."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        pl, s = _seed_plan(session, p, "adoption-2026-08")
        _create(session, p, pl, s, "a", task_id="ADO-001")

    real = task_service._next_task_id
    calls: list[str] = []

    def stale_first(session: Session, project_id: int, prefix: str) -> str:
        calls.append(prefix)
        if len(calls) == 1:
            return "ADO-001"
        return real(session, project_id, prefix)

    monkeypatch.setattr(task_service, "_next_task_id", stale_first)
    with transactional(factory) as session:
        task = _create(session, p, pl, s, "b", id_prefix="ADO")
        assert task.task_id == "ADO-002"

    with transactional(factory) as session:
        assert _count_tasks(session, p) == 2


def test_auto_id_gives_up_after_three(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        pl, s = _seed_plan(session, p, "adoption-2026-08")
        _create(session, p, pl, s, "a", task_id="ADO-001")

    next_calls: list[str] = []

    def always_taken(session: Session, project_id: int, prefix: str) -> str:
        next_calls.append(prefix)
        return "ADO-001"

    real_add = TaskRepository.add
    add_calls: list[str | None] = []

    def counting_add(self: TaskRepository, task: Task) -> Task:
        add_calls.append(task.task_id)
        return real_add(self, task)

    monkeypatch.setattr(task_service, "_next_task_id", always_taken)
    monkeypatch.setattr(TaskRepository, "add", counting_add)
    with (
        pytest.raises(task_service.DuplicateTaskIdError) as info,
        transactional(factory) as session,
    ):
        _create(session, p, pl, s, "b", id_prefix="ADO")

    assert info.value.task_id == "ADO-001"
    # Три попытки insert, у каждой свой расчёт номера; четвёртый вызов —
    # свежий next_free_id для текста ошибки, не попытка.
    assert add_calls == ["ADO-001", "ADO-001", "ADO-001"]
    assert len(next_calls) == 3 + 1


def test_savepoint_does_not_break_outer_rollback(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Регресс STO-022: insert в savepoint откатывается вместе с внешней транзакцией."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        pl, s = _seed_plan(session, p, "adoption-2026-08")
        _create(session, p, pl, s, "a", task_id="ADO-001")

    with transactional(factory) as session:
        before = _count_tasks(session, p)
    assert before == 1

    with pytest.raises(ValueError, match="NOPE-999"), transactional(factory) as session:
        _create(session, p, pl, s, "b", id_prefix="ADO", blocked_by=["NOPE-999"])

    with transactional(factory) as session:
        assert _count_tasks(session, p) == 1


def test_web_uses_service_prefix_function() -> None:
    """Критерий 5: веб и сервис выводят префикс одной функцией."""
    from cod_doc.api.web.pages import project, stories

    assert not hasattr(stories, "_id_prefix_from_plan_scope")
    assert not hasattr(project, "_id_prefix_from_scope")
    assert stories.id_prefix_from_scope is task_service.id_prefix_from_scope
    assert project.id_prefix_from_scope is task_service.id_prefix_from_scope
