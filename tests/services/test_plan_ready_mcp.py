"""AFT-003: MCP plan_ready is compact by default, bodies only on include_body=True."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Plan, PlanSection, Priority, TaskType
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(
    session: Session, task_ids: list[str], foreign_files: dict[str, list[str]] | None = None
) -> int:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="pr", title="pr", root_path="/tmp/pr", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    plan = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope="pr-plan", principle="test-first")
    )
    plan.created = now
    plan.last_updated = now
    session.flush()
    section = PlanSectionRepository(session).add(
        PlanSection(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    )
    session.flush()
    for task_id in task_ids:
        task_service.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            task_id=task_id,
            title=f"TITLE-{task_id}",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:test",
            description="DESC-LITERAL",
            acceptance="AC-1",
            affected_files=(foreign_files or {}).get(task_id),
        )
    return proj.row_id


def _plan_ready(
    monkeypatch,  # type: ignore[no-untyped-def]
    engine,  # type: ignore[no-untyped-def]
    task_ids: list[str],
    foreign_files: dict[str, list[str]] | None = None,
) -> Any:
    from cod_doc.mcp.tools import plan_tools

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        proj_id = _seed(s, task_ids, foreign_files)
    monkeypatch.setattr(plan_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(plan_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    plan_tools.register(mcp)
    return mcp._tool_manager._tools["plan_ready"].fn


def test_plan_ready_default_is_compact(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    plan_ready = _plan_ready(monkeypatch, engine_with_schema, ["PR-001"])

    result = plan_ready(project="pr", plan_scope="pr-plan")

    rows = result["tasks"]
    assert [(r["task_id"], r["title"]) for r in rows] == [("PR-001", "TITLE-PR-001")]
    for row in rows:
        assert "description" not in row
        assert "acceptance" not in row
    assert result["skipped_foreign"] == 0


def test_plan_ready_include_body_returns_bodies(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    plan_ready = _plan_ready(monkeypatch, engine_with_schema, ["PR-001"])

    result = plan_ready(project="pr", plan_scope="pr-plan", include_body=True)

    rows = result["tasks"]
    assert len(rows) == 1
    assert rows[0]["task_id"] == "PR-001"
    assert rows[0]["description"] == "DESC-LITERAL"
    assert "AC-1" in rows[0]["acceptance"]


@pytest.mark.parametrize("include_body", [False, True])
def test_plan_ready_limit_still_applies(engine_with_schema, monkeypatch, include_body) -> None:  # type: ignore[no-untyped-def]
    plan_ready = _plan_ready(monkeypatch, engine_with_schema, ["PR-001", "PR-002", "PR-003"])

    result = plan_ready(project="pr", plan_scope="pr-plan", limit=2, include_body=include_body)

    assert len(result["tasks"]) == 2


def test_plan_ready_skips_foreign(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AFT-012 (RFC 27 F13): задача со всеми файлами вне root_path проекта — чужая.

    root_path сида — '/tmp/pr'; у FRG-001 единственный affected_file
    '/elsewhere/a.py'. При local_only=True (default) она отбрасывается и
    считается в skipped_foreign; при local_only=False ready-множество полное.
    """
    plan_ready = _plan_ready(
        monkeypatch,
        engine_with_schema,
        ["LOC-001", "FRG-001"],
        foreign_files={"FRG-001": ["/elsewhere/a.py"]},
    )

    result = plan_ready(project="pr", plan_scope="pr-plan")

    assert [r["task_id"] for r in result["tasks"]] == ["LOC-001"]
    assert result["skipped_foreign"] == 1

    result = plan_ready(project="pr", plan_scope="pr-plan", local_only=False)

    assert [r["task_id"] for r in result["tasks"]] == ["FRG-001", "LOC-001"]
    assert result["skipped_foreign"] == 0
