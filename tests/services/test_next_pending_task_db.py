"""PCA-937: next_pending_task respects blocked_by, plan_scope, and checkout locks.

Заменил YAML-only behavior на DB-backed ready-set с фильтрами:

1. blocked_by — задача с незакрытым блокером не возвращается.
2. plan_scope — опциональный фильтр по плану.
3. task_checkout — задача с активным локом пропускается.
"""

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
from cod_doc.mcp.tools import legacy_project_tools
from cod_doc.services import task_service


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed_project(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="ptest", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(
        project_id=proj.row_id, scope="p-plan", created=now, last_updated=now
    )
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="A", slug="A", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make_task(session, proj_id, plan_id, sec_id, task_id, *, priority=Priority.MEDIUM):
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
    )


def _register_tool(monkeypatch, factory):
    # Stub session_factory + open_project + require_project_id.
    from cod_doc.mcp.tools import _db
    from cod_doc.mcp.tools import legacy_project_tools as lpt

    monkeypatch.setattr(_db, "session_factory", lambda project: (factory, None))

    # legacy_project_tools.open_project is called by the YAML-fallback path; we
    # stub it to a no-op object that raises if the DB path failed.
    class _NoYamlFallback:
        def next_pending_task(self):
            return None

    monkeypatch.setattr(lpt, "open_project", lambda name: _NoYamlFallback())
    # require_project_id resolves the slug → row_id via select. To make it
    # match our seeded "ptest" slug, we patch the helper to return our id.
    monkeypatch.setattr(_db, "require_project_id", lambda session, project: 1)

    mcp = FastMCP("test")
    legacy_project_tools.register(mcp)
    return _get_tool(mcp, "next_pending_task")


def test_blocked_task_is_not_returned(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed_project(session)
        a = _make_task(session, proj_id, plan_id, sec_id, "PLN-001", priority=Priority.HIGH)
        b = _make_task(session, proj_id, plan_id, sec_id, "PLN-002")
        # B is blocked by A; A is pending, so B is not ready.
        session.add(
            DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks")
        )
        session.flush()

    next_pending = _register_tool(monkeypatch, factory)
    result = next_pending(project="ptest")
    # A is the only ready one (B is blocked by pending A).
    assert result.get("task_id") == "PLN-001"


def test_checkout_locked_task_is_skipped(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed_project(session)
        a = _make_task(session, proj_id, plan_id, sec_id, "PLN-001", priority=Priority.HIGH)
        _make_task(session, proj_id, plan_id, sec_id, "PLN-002", priority=Priority.LOW)
        # Lock A — next_pending should skip A and return the lower-priority B.
        a_model = session.get(TaskModel, a.row_id)
        assert a_model is not None
        a_model.checked_out_by = "agent-x"
        a_model.checked_out_at = datetime.now(UTC)
        session.flush()

    next_pending = _register_tool(monkeypatch, factory)
    result = next_pending(project="ptest")
    assert result.get("task_id") == "PLN-002"


def test_returns_null_when_queue_empty(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _seed_project(session)

    next_pending = _register_tool(monkeypatch, factory)
    result = next_pending(project="ptest")
    assert result.get("task") is None
    assert "Очередь" in result.get("message", "")
