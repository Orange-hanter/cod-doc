"""Cycle-4: task_next_ready — renamed legacy `next_pending_task` with clearer semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.mcp.tools import task_tools
from cod_doc.services import task_service


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="nr", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="nr-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(
    session, proj_id, plan_id, sec_id, task_id, *, priority=Priority.MEDIUM, affected_files=None
):
    return task_service.create(
        session,
        project_id=proj_id,
        plan_id=plan_id,
        section_id=sec_id,
        task_id=task_id,
        title=task_id,
        type=TaskType.FEATURE,
        priority=priority,
        author="t",
        affected_files=affected_files,
    )


def _setup(monkeypatch, factory):
    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    return _get_tool(mcp, "task_next_ready")


def test_task_next_ready_skips_blocked(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed(session)
        a = _make(session, proj_id, plan_id, sec_id, "NRD-001", priority=Priority.HIGH)
        b = _make(session, proj_id, plan_id, sec_id, "NRD-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

    task_next_ready = _setup(monkeypatch, factory)
    result = task_next_ready(project="nr")
    assert result["task_id"] == "NRD-001"  # A is the only ready task


def test_task_next_ready_skips_checkout_locked(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed(session)
        a = _make(session, proj_id, plan_id, sec_id, "NRD-001", priority=Priority.HIGH)
        _make(session, proj_id, plan_id, sec_id, "NRD-002", priority=Priority.LOW)
        a_m = session.get(TaskModel, a.row_id)
        assert a_m is not None
        a_m.checked_out_by = "other-agent"
        a_m.checked_out_at = datetime.now(UTC)
        session.flush()

    task_next_ready = _setup(monkeypatch, factory)
    result = task_next_ready(project="nr")
    assert result["task_id"] == "NRD-002"


def test_task_next_ready_empty_returns_none(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    task_next_ready = _setup(monkeypatch, factory)
    result = task_next_ready(project="nr")
    assert result is None


def test_next_ready_skips_foreign_by_default(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AFT-012 / RFC 27 F13: foreign task (all files outside root_path) is
    skipped even with a higher priority; skipped_foreign counts it."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed(session)
        _make(
            session,
            proj_id,
            plan_id,
            sec_id,
            "NRD-003",
            priority=Priority.CRITICAL,
            affected_files=["/elsewhere/a.py"],
        )
        _make(session, proj_id, plan_id, sec_id, "NRD-004", priority=Priority.LOW)

    task_next_ready = _setup(monkeypatch, factory)
    result = task_next_ready(project="nr")
    assert result["task_id"] == "NRD-004"
    assert result["skipped_foreign"] == 1


def test_next_ready_local_only_false_returns_foreign(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed(session)
        _make(
            session,
            proj_id,
            plan_id,
            sec_id,
            "NRD-003",
            priority=Priority.CRITICAL,
            affected_files=["/elsewhere/a.py"],
        )
        _make(session, proj_id, plan_id, sec_id, "NRD-004", priority=Priority.LOW)

    task_next_ready = _setup(monkeypatch, factory)
    result = task_next_ready(project="nr", local_only=False)
    assert result["task_id"] == "NRD-003"
    assert result["skipped_foreign"] == 0


def test_next_ready_none_when_only_foreign(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed(session)
        _make(
            session,
            proj_id,
            plan_id,
            sec_id,
            "NRD-003",
            priority=Priority.CRITICAL,
            affected_files=["/elsewhere/a.py"],
        )

    task_next_ready = _setup(monkeypatch, factory)
    assert task_next_ready(project="nr") is None
    result = task_next_ready(project="nr", local_only=False)
    assert result["task_id"] == "NRD-003"
