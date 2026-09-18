"""Перенос задачи между секциями плана — ``task_service.move_to_section``.

Секции плана были единственной группировкой бэклога, которую нельзя изменить
после создания задачи: ``create`` принимает ``section_id``, а ни одна мутация
его не трогала. Оставался прямой SQL — без ревизии и без события, после чего
``revision_revert`` и ``plan audit`` начинают врать.

Два уровня здесь: service (`move_to_section`) и MCP (`task_move_to_section`
поверх него). CLI-эквивалент (`cod-doc task move`) покрыт
tests/cli/test_task_move.py; паритет поверхностей стережёт
tests/services/test_task_mutation_surface_parity.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, Task, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks
from cod_doc.services.task_service import (
    CrossPlanMoveError,
    SectionNotFoundError,
    TaskNotFoundError,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> tuple[int, int, int, int]:
    """Проект + план с двумя секциями A и B. Возвращает (project, plan, A, B)."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="mv", title="MV", root_path="/tmp/mv", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="mv-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec_a = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
    )
    sec_b = PlanSectionModel(
        plan_id=plan.row_id, letter="B", title="Web UI", slug="B-Web-UI", position=1
    )
    session.add_all([sec_a, sec_b])
    session.flush()
    return proj.row_id, plan.row_id, sec_a.row_id, sec_b.row_id


def _second_plan(session: Session, project_id: int) -> tuple[int, int]:
    """Второй план того же проекта с одной секцией. Возвращает (plan, section)."""
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope="mv-other", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Other", slug="A-Other", position=0
    )
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id


def _task(session: Session, p: int, pl: int, s: int, task_id: str = "MV-001") -> Task:
    return tasks.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=task_id,
        title=f"Task {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )


def test_move_writes_revision_and_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, b = _seed(session)
        task = _task(session, p, pl, a)
        assert task.row_id is not None

        moved = tasks.move_to_section(
            session,
            task_id=task.task_id,
            new_section_id=b,
            author="agent:run-X",
            reason="реструктуризация бэклога",
        )
        assert moved.section_id == b

        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        diff = json.loads(history[-1].diff)
        assert diff == {
            "op": "section",
            "old": a,
            "new": b,
            "old_letter": "A",
            "new_letter": "B",
        }
        assert history[-1].reason == "реструктуризация бэклога"

        session.flush()
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "task.section_changed",
                    ActivityEventModel.scope_id == task.task_id,
                )
            ).scalars()
        )
        assert len(events) == 1
        # ADO-040: actor_kind выводится из author, а не хардкодится.
        assert events[0].actor_kind == "agent"
        assert events[0].payload["old_letter"] == "A"
        assert events[0].payload["new_letter"] == "B"


def test_move_persists_section_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, b = _seed(session)
        task = _task(session, p, pl, a)

        tasks.move_to_section(session, task_id=task.task_id, new_section_id=b, author="human:test")

        session.flush()
        model = session.execute(
            select(TaskModel).where(TaskModel.task_id == task.task_id)
        ).scalar_one()
        assert model.section_id == b
        # plan_id остаётся прежним — перенос между планами не поддерживается.
        assert model.plan_id == pl


def test_move_to_same_section_is_noop(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        task = _task(session, p, pl, a)
        assert task.row_id is not None
        before = len(rev.list_for_entity(session, EntityKind.TASK, task.row_id))

        tasks.move_to_section(session, task_id=task.task_id, new_section_id=a, author="human:test")

        after = len(rev.list_for_entity(session, EntityKind.TASK, task.row_id))
        assert after == before


def test_move_unknown_section_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        task = _task(session, p, pl, a)

        with pytest.raises(SectionNotFoundError):
            tasks.move_to_section(
                session, task_id=task.task_id, new_section_id=999_999, author="human:test"
            )


def test_move_across_plans_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        task = _task(session, p, pl, a)
        _other_plan, other_section = _second_plan(session, p)

        with pytest.raises(CrossPlanMoveError):
            tasks.move_to_section(
                session, task_id=task.task_id, new_section_id=other_section, author="human:test"
            )


def test_move_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _p, _pl, _a, b = _seed(session)

        with pytest.raises(TaskNotFoundError):
            tasks.move_to_section(
                session, task_id="NOPE-001", new_section_id=b, author="human:test"
            )


# --------------------------------------------------------------------------- #
# MCP: task_move_to_section                                                    #
# --------------------------------------------------------------------------- #


def _tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _register(monkeypatch, factory, proj_id: int) -> FastMCP:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return mcp


def test_mcp_move_batch_moves_all(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        for n in (1, 2, 3):
            _task(session, p, pl, a, task_id=f"MV-00{n}")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    out = move(
        project="mv",
        task_ids=["MV-001", "MV-002", "MV-003"],
        plan_scope="mv-plan",
        section_letter="B",
        author="agent:test",
    )
    assert len(out["moved"]) == 3
    assert out["skipped"] == []
    assert out["errors"] == []
    assert out["committed"] is True
    assert out["section"]["title"] == "Web UI"

    with transactional(factory) as session:
        events = list(
            session.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "task.section_changed")
            ).scalars()
        )
        # Одно событие на задачу: батч не схлопывает историю.
        assert len(events) == 3


def test_mcp_move_is_case_insensitive_on_letter(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, b = _seed(session)
        _task(session, p, pl, a)

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    out = move(project="mv", task_ids=["MV-001"], plan_scope="mv-plan", section_letter="b")
    assert len(out["moved"]) == 1
    assert out["moved"][0]["section_id"] == b


def test_mcp_move_already_there_is_skipped_not_error(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        _task(session, p, pl, a)

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    out = move(project="mv", task_ids=["MV-001"], plan_scope="mv-plan", section_letter="A")
    assert out["moved"] == []
    assert out["skipped"] == ["MV-001"]
    assert out["errors"] == []


def test_mcp_move_dry_run_rolls_back(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        _task(session, p, pl, a)

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    out = move(
        project="mv",
        task_ids=["MV-001"],
        plan_scope="mv-plan",
        section_letter="B",
        dry_run=True,
    )
    assert out["dry_run"] is True
    assert out["committed"] is False
    assert len(out["moved"]) == 1

    with transactional(factory) as session:
        model = session.execute(select(TaskModel).where(TaskModel.task_id == "MV-001")).scalar_one()
        assert model.section_id == a


def test_mcp_move_rolls_back_whole_batch_on_error(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        _task(session, p, pl, a)

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    with pytest.raises(ValueError, match="NOPE-001"):
        move(
            project="mv",
            task_ids=["MV-001", "NOPE-001"],
            plan_scope="mv-plan",
            section_letter="B",
        )

    with transactional(factory) as session:
        model = session.execute(select(TaskModel).where(TaskModel.task_id == "MV-001")).scalar_one()
        assert model.section_id == a


def test_mcp_move_continue_on_error_commits_the_good_ones(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, b = _seed(session)
        _task(session, p, pl, a)

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    out = move(
        project="mv",
        task_ids=["MV-001", "NOPE-001"],
        plan_scope="mv-plan",
        section_letter="B",
        continue_on_error=True,
    )
    assert len(out["moved"]) == 1
    assert [e["task_id"] for e in out["errors"]] == ["NOPE-001"]

    with transactional(factory) as session:
        model = session.execute(select(TaskModel).where(TaskModel.task_id == "MV-001")).scalar_one()
        assert model.section_id == b


def test_mcp_move_unknown_section_raises(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, a, _b = _seed(session)
        _task(session, p, pl, a)

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    with pytest.raises(ValueError, match="Section 'Z' not found"):
        move(project="mv", task_ids=["MV-001"], plan_scope="mv-plan", section_letter="Z")


def test_mcp_move_empty_task_ids_raises(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _pl, _a, _b = _seed(session)

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_section")
    with pytest.raises(ValueError, match="task_ids is empty"):
        move(project="mv", task_ids=[], plan_scope="mv-plan", section_letter="B")
