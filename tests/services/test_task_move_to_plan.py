"""Перенос задач между планами — ``task_service.move_to_plan`` (ADO-243).

Распил разросшегося плана (``adoption-2026-08``: 286 задач, свалка в секции D)
упирался в то, что сменить план задачи было нечем: ``move_to_section``
запрещает это намеренно, а пересоздание в новом плане теряет историю —
ревизии висят на ``row_id``.

Здесь стережётся главное обещание операции — **идентичность задачи
переживает перенос**: тот же ``row_id`` и ``task_id``, ревизии, документы
задачи, зависимости, замок и статус. Плюс три правила владельца: явный
``plan.id_prefix`` выигрывает у вывода по задачам, кросс-плановое ребро —
предупреждение, а не запрет, чужой замок — отказ.

Сервис и MCP (`task_move_to_plan`) — здесь, CLI — tests/cli/test_task_move_plan.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, Task, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import checkout_service, plan_service, task_doc_service
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks
from cod_doc.services.checkout_service import CheckoutConflictError
from cod_doc.services.revision_service import RevertNotSupportedError
from cod_doc.services.serializers import task_to_dict
from cod_doc.services.task_service import CrossPlanMoveError, SectionNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _plan(session: Session, project_id: int, scope: str, letters: str = "A") -> dict[str, int]:
    """План с секциями по буквам. Возвращает {"plan": id, "A": section_id, ...}."""
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope=scope, created=now, last_updated=now)
    session.add(plan)
    session.flush()
    out = {"plan": plan.row_id}
    for pos, letter in enumerate(letters):
        sec = PlanSectionModel(
            plan_id=plan.row_id,
            letter=letter,
            title=f"Sec {letter}",
            slug=f"{letter}-Sec",
            position=pos,
        )
        session.add(sec)
        session.flush()
        out[letter] = sec.row_id
    return out


def _seed(session: Session) -> tuple[int, dict[str, int], dict[str, int]]:
    """Проект, исходный план `old` (A, B) и целевой `new` (A, B)."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="mp", title="MP", root_path="/tmp/mp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return (
        proj.row_id,
        _plan(session, proj.row_id, "old", "AB"),
        _plan(session, proj.row_id, "new", "AB"),
    )


def _task(session: Session, p: int, plan: dict[str, int], letter: str, task_id: str) -> Task:
    return tasks.create(
        session,
        project_id=p,
        plan_id=plan["plan"],
        section_id=plan[letter],
        task_id=task_id,
        title=f"Implement: {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )


def _move(session: Session, task_id: str, section_id: int, **kw: Any) -> Task:
    return tasks._move_one_to_plan(
        session, task_id=task_id, new_section_id=section_id, author="agent:groom", **kw
    )


# --------------------------------------------------------------------------- #
# Service: identity survives                                                   #
# --------------------------------------------------------------------------- #


def test_move_keeps_row_id_task_id_status_and_history(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        task = _task(session, p, old, "A", "ADO-001")
        assert task.row_id is not None
        tasks.update_status(
            session, task_id="ADO-001", new_status=TaskStatus.BLOCKED, author="human:test"
        )
        before = len(rev.list_for_entity(session, EntityKind.TASK, task.row_id))

        moved = _move(session, "ADO-001", new["B"], reason="распил плана")

        assert moved.row_id == task.row_id
        assert moved.task_id == "ADO-001"
        assert moved.plan_id == new["plan"]
        assert moved.section_id == new["B"]
        assert moved.status == TaskStatus.BLOCKED
        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        assert len(history) == before + 1, "старые ревизии остались на той же задаче"
        assert json.loads(history[-1].diff) == {
            "op": "plan",
            "old_plan": "old",
            "new_plan": "new",
            "old_section": old["A"],
            "new_section": new["B"],
            "old_letter": "A",
            "new_letter": "B",
        }
        assert history[-1].reason == "распил плана"


def test_move_keeps_task_documents(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        task = _task(session, p, old, "A", "ADO-001")
        assert task.row_id is not None
        task_doc_service.put(
            session,
            project_id=p,
            task_row_id=task.row_id,
            key="contract",
            title="Контракт",
            body="тело контракта",
            author="human:test",
        )

        _move(session, "ADO-001", new["A"])

        doc = task_doc_service.get(session, task.row_id, "contract")
        assert doc is not None
        assert doc.body == "тело контракта"


def test_move_keeps_dependency_and_warns_about_the_cross_plan_edge(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ребро разрешено (ready_tasks глобален), но графы плана его не видят."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        _task(session, p, old, "A", "ADO-002")
        tasks.add_dependency(
            session,
            project_id=p,
            task_id="ADO-002",
            blocker_task_id="ADO-001",
            note="002 строится поверх 001",
            author="human:test",
        )

        _move(session, "ADO-002", new["A"])

        moved = tasks.get(session, "ADO-002")
        assert moved is not None
        assert task_to_dict(moved, session)["blocked_by"] == ["ADO-001"]
        warnings = tasks.cross_plan_dependency_warnings(session, project_id=p, task_ids=["ADO-002"])
        assert len(warnings) == 1
        assert "ADO-002 (new)" in warnings[0]
        assert "ADO-001 (old)" in warnings[0]


def test_same_plan_edge_gives_no_warning(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        _task(session, p, old, "A", "ADO-002")
        tasks.add_dependency(
            session,
            project_id=p,
            task_id="ADO-002",
            blocker_task_id="ADO-001",
            note="связаны",
            author="human:test",
        )
        _move(session, "ADO-001", new["A"])
        _move(session, "ADO-002", new["A"])

        assert (
            tasks.cross_plan_dependency_warnings(
                session, project_id=p, task_ids=["ADO-001", "ADO-002"]
            )
            == []
        )


def test_totals_of_both_plans_follow_the_move(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        _task(session, p, old, "A", "ADO-002")

        _move(session, "ADO-001", new["A"])

        totals = {pp.scope: pp.total for pp in plan_service.progress_for_project(session, p)}
        assert totals == {"old": 1, "new": 1}


def test_move_emits_event_and_bumps_both_plans(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        stamp = datetime(2020, 1, 1, tzinfo=UTC)
        for plan_id in (old["plan"], new["plan"]):
            plan_model = session.get(PlanModel, plan_id)
            assert plan_model is not None
            plan_model.last_updated = stamp
        session.flush()

        _move(session, "ADO-001", new["A"])
        session.flush()

        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "task.plan_changed",
                    ActivityEventModel.scope_id == "ADO-001",
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].actor_kind == "agent"
        assert events[0].payload["old_plan"] == "old"
        assert events[0].payload["new_plan"] == "new"
        for plan_id in (old["plan"], new["plan"]):
            plan_model = session.get(PlanModel, plan_id)
            assert plan_model is not None
            assert plan_model.last_updated.replace(tzinfo=UTC) > stamp


def test_done_task_moves_too(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        tasks.complete(session, task_id="ADO-001", author="human:test", commit_sha="abc1234")

        moved = _move(session, "ADO-001", new["A"])

        assert moved.status == TaskStatus.DONE
        assert moved.completed_commit == "abc1234"


# --------------------------------------------------------------------------- #
# Service: rules                                                               #
# --------------------------------------------------------------------------- #


def test_foreign_lock_refuses_the_move(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Задачу нельзя вырвать из-под исполнителя — сначала явный task_release."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        checkout_service.checkout(session, "ADO-001", agent="swarm-kimi")

        with pytest.raises(CheckoutConflictError):
            _move(session, "ADO-001", new["A"])

        row = session.execute(select(TaskModel).where(TaskModel.task_id == "ADO-001")).scalar_one()
        assert row.plan_id == old["plan"]


def test_own_lock_moves_and_keeps_the_lock(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        checkout_service.checkout(session, "ADO-001", agent="agent:groom")

        _move(session, "ADO-001", new["A"])

        row = session.execute(select(TaskModel).where(TaskModel.task_id == "ADO-001")).scalar_one()
        assert row.plan_id == new["plan"]
        assert row.checked_out_by == "agent:groom"


def test_same_plan_target_is_refused(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")

        with pytest.raises(CrossPlanMoveError, match="move_to_section"):
            _move(session, "ADO-001", old["B"])


def test_other_project_target_is_refused(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        now = datetime.now(UTC)
        other = ProjectModel(slug="zz", title="ZZ", root_path="/tmp/zz", config_json={})
        other.created = now
        other.updated = now
        session.add(other)
        session.flush()
        foreign = _plan(session, other.row_id, "foreign", "A")

        with pytest.raises(CrossPlanMoveError, match="another project"):
            _move(session, "ADO-001", foreign["A"])


def test_unknown_section_is_refused(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")

        with pytest.raises(SectionNotFoundError):
            _move(session, "ADO-001", 999_999)


def test_revert_of_a_plan_move_is_an_explicit_refusal(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Откат — обратный перенос, а не revision_revert."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        task = _task(session, p, old, "A", "ADO-001")
        assert task.row_id is not None
        _move(session, "ADO-001", new["A"])
        last = rev.list_for_entity(session, EntityKind.TASK, task.row_id)[-1]

        with pytest.raises(RevertNotSupportedError):
            rev.revert(session, last.revision_id, author="human:test")


# --------------------------------------------------------------------------- #
# plan.id_prefix                                                               #
# --------------------------------------------------------------------------- #


def test_explicit_plan_prefix_beats_the_moved_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ровно сценарий распила: ADO-* переехали в план WEB — новые задачи WEB-*."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        web = plan_service.create_plan(
            session,
            project_id=p,
            scope="web-ui",
            principle="track W",
            author="human:test",
            id_prefix="WEB",
        )
        assert web.row_id is not None
        sec = plan_service.create_section(
            session,
            project_id=p,
            plan_scope="web-ui",
            letter="A",
            title="Pages",
            author="human:test",
        )
        for n in (1, 2, 3):
            _task(session, p, old, "A", f"ADO-00{n}")
            _move(session, f"ADO-00{n}", sec.row_id)

        assert tasks.id_prefix_for_plan(session, web.row_id) == "WEB"


def test_without_explicit_prefix_the_moved_tasks_win(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Фолбэк прежний: по самому частому префиксу задач плана."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        for n in (1, 2):
            _task(session, p, old, "A", f"ADO-00{n}")
            _move(session, f"ADO-00{n}", new["A"])

        assert tasks.id_prefix_for_plan(session, new["plan"]) == "ADO"


def test_invalid_plan_prefix_is_rejected(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.services.validation import ValidationError

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, _old, _new = _seed(session)
        with pytest.raises(ValidationError):
            plan_service.create_plan(
                session,
                project_id=p,
                scope="bad",
                principle="x",
                author="human:test",
                id_prefix="web",
            )


# --------------------------------------------------------------------------- #
# Service batch + MCP: task_move_to_plan                                      #
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


def test_mcp_moves_a_batch_with_one_event_per_task(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        for n in (1, 2, 3):
            _task(session, p, old, "A", f"ADO-00{n}")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    out = move(
        project="mp",
        task_ids=["ADO-001", "ADO-002", "ADO-003"],
        plan_scope="new",
        section_letter="b",
        author="agent:groom",
    )
    assert [t["task_id"] for t in out["moved"]] == ["ADO-001", "ADO-002", "ADO-003"]
    assert out["section"] == {"letter": "B", "title": "Sec B", "plan_scope": "new"}
    assert out["committed"] is True
    assert out["warnings"] == []

    with transactional(factory) as session:
        n_events = len(
            list(
                session.execute(
                    select(ActivityEventModel).where(ActivityEventModel.kind == "task.plan_changed")
                ).scalars()
            )
        )
        assert n_events == 3


def test_mcp_from_section_moves_the_whole_section(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "B", "ADO-002")
        _task(session, p, old, "B", "ADO-001")
        _task(session, p, old, "A", "ADO-009")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    out = move(project="mp", from_section="old:B", plan_scope="new", section_letter="A")
    assert [t["task_id"] for t in out["moved"]] == ["ADO-001", "ADO-002"]

    with transactional(factory) as session:
        stays = session.execute(
            select(TaskModel.plan_id).where(TaskModel.task_id == "ADO-009")
        ).scalar_one()
        assert stays != out["moved"][0]["plan_id"]


def test_mcp_requires_exactly_one_selector(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    with pytest.raises(ValueError, match="exactly one"):
        move(project="mp", plan_scope="new", section_letter="A")
    with pytest.raises(ValueError, match="exactly one"):
        move(
            project="mp",
            task_ids=["ADO-001"],
            from_section="old:A",
            plan_scope="new",
            section_letter="A",
        )


def test_mcp_dry_run_writes_nothing(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    out = move(
        project="mp", task_ids=["ADO-001"], plan_scope="new", section_letter="A", dry_run=True
    )
    assert out["dry_run"] is True
    assert out["committed"] is False
    assert len(out["moved"]) == 1

    with transactional(factory) as session:
        plan_id = session.execute(
            select(TaskModel.plan_id).where(TaskModel.task_id == "ADO-001")
        ).scalar_one()
        assert plan_id == old["plan"]
        assert (
            list(
                session.execute(
                    select(ActivityEventModel).where(ActivityEventModel.kind == "task.plan_changed")
                ).scalars()
            )
            == []
        )


def test_mcp_foreign_lock_rolls_back_the_whole_batch(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        _task(session, p, old, "A", "ADO-002")
        checkout_service.checkout(session, "ADO-002", agent="swarm-kimi")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    with pytest.raises(ValueError, match="swarm-kimi"):
        move(project="mp", task_ids=["ADO-001", "ADO-002"], plan_scope="new", section_letter="A")

    with transactional(factory) as session:
        plans = set(
            session.execute(
                select(TaskModel.plan_id).where(TaskModel.task_id.in_(["ADO-001", "ADO-002"]))
            ).scalars()
        )
        assert plans == {old["plan"]}, "первая задача не должна была переехать в одиночку"


def test_mcp_continue_on_error_moves_the_rest(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        _task(session, p, old, "A", "ADO-002")
        checkout_service.checkout(session, "ADO-002", agent="swarm-kimi")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    out = move(
        project="mp",
        task_ids=["ADO-001", "ADO-002", "NOPE-999"],
        plan_scope="new",
        section_letter="A",
        continue_on_error=True,
    )
    assert [t["task_id"] for t in out["moved"]] == ["ADO-001"]
    assert [e["task_id"] for e in out["errors"]] == ["ADO-002", "NOPE-999"]
    assert out["committed"] is True


def test_mcp_already_there_is_skipped(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, new = _seed(session)
        _task(session, p, old, "A", "ADO-001")
        _move(session, "ADO-001", new["A"])

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    out = move(project="mp", task_ids=["ADO-001"], plan_scope="new", section_letter="A")
    assert out["moved"] == []
    assert out["skipped"] == ["ADO-001"]


def test_mcp_unknown_target_plan_raises(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, old, _new = _seed(session)
        _task(session, p, old, "A", "ADO-001")

    move = _tool(_register(monkeypatch, factory, p), "task_move_to_plan")
    with pytest.raises(ValueError, match="not found"):
        move(project="mp", task_ids=["ADO-001"], plan_scope="nope", section_letter="A")
