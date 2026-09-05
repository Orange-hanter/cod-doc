"""ADO-067: grooming задачи — description / acceptance / priority.

Три уровня:

* service — ``update_priority`` (новая функция): revision + activity event
  по правилу ADO-040, no-op на неизменившемся значении;
* MCP — тул ``task_update`` поверх трёх сервисных функций;
* сериализация — приоритет доезжает до payload.

CLI-эквивалент (`cod-doc task update`) покрыт tests/cli/test_task_update.py,
паритет поверхностей стережёт
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
)
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks
from cod_doc.services.task_service import TaskNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_plan(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="gr", title="GR", root_path="/tmp/gr", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="gr-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _task(session: Session, p: int, pl: int, s: int, task_id: str = "GRM-001") -> Task:
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
        description="старая формулировка",
        acceptance="старый критерий",
    )


# --------------------------------------------------------------------------- #
# service: update_priority                                                     #
# --------------------------------------------------------------------------- #


def test_update_priority_writes_revision_and_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        assert task.row_id is not None

        updated = tasks.update_priority(
            session,
            task_id=task.task_id,
            new_priority=Priority.CRITICAL,
            author="agent:run-X",
            reason="переоценка спринта",
        )
        assert updated.priority is Priority.CRITICAL

        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        diff = json.loads(history[-1].diff)
        assert diff == {"op": "priority", "old": "medium", "new": "critical"}
        assert history[-1].reason == "переоценка спринта"

        session.flush()
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "task.priority_changed",
                    ActivityEventModel.scope_id == task.task_id,
                )
            ).scalars()
        )
        assert len(events) == 1
        # ADO-040: actor_kind выводится из author, а не хардкодится.
        assert events[0].actor_kind == "agent"
        assert events[0].payload["old_priority"] == "medium"
        assert events[0].payload["new_priority"] == "critical"


def test_update_priority_same_value_is_noop(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _task(session, p, pl, s)
        assert task.row_id is not None
        before = len(rev.list_for_entity(session, EntityKind.TASK, task.row_id))

        tasks.update_priority(
            session,
            task_id=task.task_id,
            new_priority=Priority.MEDIUM,
            author="human:test",
        )

        after = len(rev.list_for_entity(session, EntityKind.TASK, task.row_id))
        assert after == before, "no-op не должен писать ревизию"


def test_update_priority_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_plan(session)
        with pytest.raises(TaskNotFoundError):
            tasks.update_priority(
                session,
                task_id="NOPE-999",
                new_priority=Priority.LOW,
                author="human:test",
            )


# --------------------------------------------------------------------------- #
# MCP: task_update                                                             #
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


def test_mcp_task_update_changes_all_three_fields(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _task(session, p, pl, s)

    task_update = _tool(_register(monkeypatch, factory, p), "task_update")
    out = task_update(
        project="gr",
        task_id="GRM-001",
        description="новая формулировка скоупа",
        acceptance="новый критерий приёмки",
        priority="high",
        author="agent:run-Y",
        reason="грумминг",
    )

    assert out["description"] == "новая формулировка скоупа"
    assert out["acceptance"] == "новый критерий приёмки"
    assert out["priority"] == "high"
    assert out["updated_fields"] == ["description", "acceptance", "priority"]

    with transactional(factory) as session:
        t = tasks.get(session, "GRM-001")
        assert t is not None
        assert t.priority is Priority.HIGH
        assert t.description == "новая формулировка скоупа"
        kinds = set(
            session.execute(
                select(ActivityEventModel.kind).where(ActivityEventModel.scope_id == "GRM-001")
            ).scalars()
        )
    assert {
        "task.description_updated",
        "task.acceptance_updated",
        "task.priority_changed",
    } <= kinds


def test_mcp_task_update_partial_leaves_other_fields(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _task(session, p, pl, s)

    task_update = _tool(_register(monkeypatch, factory, p), "task_update")
    out = task_update(project="gr", task_id="GRM-001", priority="low")

    assert out["updated_fields"] == ["priority"]
    assert out["description"] == "старая формулировка"
    assert out["acceptance"] == "старый критерий"


def test_mcp_task_update_dry_run_rolls_back(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _task(session, p, pl, s)

    task_update = _tool(_register(monkeypatch, factory, p), "task_update")
    out = task_update(project="gr", task_id="GRM-001", priority="critical", dry_run=True)
    assert out["dry_run"] is True

    with transactional(factory) as session:
        t = tasks.get(session, "GRM-001")
        assert t is not None
        assert t.priority is Priority.MEDIUM, "dry_run не должен коммитить"


def test_mcp_task_update_requires_at_least_one_field(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _pl, _s = _seed_plan(session)

    task_update = _tool(_register(monkeypatch, factory, p), "task_update")
    with pytest.raises(ValueError, match="at least one"):
        task_update(project="gr", task_id="GRM-001")


def test_mcp_task_update_rejects_unknown_priority(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _pl, _s = _seed_plan(session)

    task_update = _tool(_register(monkeypatch, factory, p), "task_update")
    with pytest.raises(ValueError, match="Unknown priority"):
        task_update(project="gr", task_id="GRM-001", priority="urgent")


def test_mcp_task_update_unknown_task_raises_value_error(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _pl, _s = _seed_plan(session)

    task_update = _tool(_register(monkeypatch, factory, p), "task_update")
    with pytest.raises(ValueError, match="not found"):
        task_update(project="gr", task_id="NOPE-999", priority="low")
