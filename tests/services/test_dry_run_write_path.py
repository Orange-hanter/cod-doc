"""PCA-944: dry_run=True на write-тулах валидирует, но не коммитит.

Проверяем по одному representative тулу из каждой категории:
- task_create (DB-create) — task не остаётся в DB.
- task_update_status (DB-update) — status не меняется.
- doc_create — doc не остаётся в DB.

Транзакционность гарантирована новым commit-параметром в `transactional()`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import (
    Priority,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DocumentModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.mcp.tools import doc_tools, task_tools
from cod_doc.services import task_service


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _stub(monkeypatch, factory, *, module, require_id: int = 1) -> None:
    monkeypatch.setattr(module, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(module, "require_project_id", lambda session, project: require_id)


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def test_task_create_dry_run_does_not_persist(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    _stub(monkeypatch, factory, module=task_tools)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    task_create = _get_tool(mcp, "task_create")

    result = task_create(
        project="p",
        plan_scope="p-plan",
        section_letter="A",
        title="dry-run task",
        type="feature",
        priority="medium",
        id_prefix="DRY",
        dry_run=True,
    )

    assert result.get("dry_run") is True
    assert result.get("task_id"), "dry_run должен вернуть would-be task_id"

    # DB must NOT carry the row.
    with transactional(factory) as session:
        rows = session.execute(select(TaskModel).where(TaskModel.title == "dry-run task")).all()
    assert rows == [], "dry_run=True committed the task to DB"


def test_task_create_real_run_persists(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    _stub(monkeypatch, factory, module=task_tools)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    task_create = _get_tool(mcp, "task_create")

    result = task_create(
        project="p",
        plan_scope="p-plan",
        section_letter="A",
        title="real task",
        type="feature",
        priority="medium",
        id_prefix="WET",
    )

    assert "dry_run" not in result
    with transactional(factory) as session:
        rows = session.execute(select(TaskModel).where(TaskModel.title == "real task")).all()
    assert len(rows) == 1


def test_task_update_status_dry_run_does_not_change_status(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id, plan_id, sec_id = _seed(session)
        task_service.create(
            session,
            project_id=proj_id,
            plan_id=plan_id,
            section_id=sec_id,
            task_id="PLN-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="t",
        )
        # Move to in_progress so we can test cancel transition.
        task_service.update_status(
            session,
            task_id="PLN-001",
            new_status=TaskStatus.IN_PROGRESS_NEW,
            author="t",
            via_checkout=True,
        )

    _stub(monkeypatch, factory, module=task_tools)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    update_status = _get_tool(mcp, "task_update_status")

    result = update_status(project="p", task_id="PLN-001", new_status="cancelled", dry_run=True)
    assert result.get("dry_run") is True

    with transactional(factory) as session:
        row = session.execute(select(TaskModel).where(TaskModel.task_id == "PLN-001")).scalar_one()
        # status must NOT be cancelled — dry_run rolled back.
        assert row.status != TaskStatus.CANCELLED


def test_doc_create_dry_run_does_not_persist(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    _stub(monkeypatch, factory, module=doc_tools)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    doc_create = _get_tool(mcp, "doc_create")

    result = doc_create(
        project="p",
        doc_key="dry/key",
        type="guide",
        status="draft",
        title="dry doc",
        dry_run=True,
    )
    assert result.get("dry_run") is True

    with transactional(factory) as session:
        rows = session.execute(
            select(DocumentModel).where(DocumentModel.doc_key == "dry/key")
        ).all()
    assert rows == [], "dry_run=True committed the doc"
