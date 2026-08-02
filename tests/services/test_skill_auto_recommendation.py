"""PCA-949: tool responses augmented with recommended_skills."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.mcp.tools import _idempotency, task_tools
from cod_doc.services.skill_service import recommend_for_tool


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="sr", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="sr-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def test_recommend_for_tool_task_create_includes_task_standard() -> None:
    recs = recommend_for_tool("task_create")
    assert "task-standard" in recs, f"task-standard skill must match task_create; got {recs}"


def test_recommend_for_tool_doc_create_includes_doc_style() -> None:
    recs = recommend_for_tool("doc_create")
    assert "doc-style" in recs or "validation" in recs, (
        f"expected doc-style or validation skill; got {recs}"
    )


def test_task_create_response_carries_recommended_skills(
    engine_with_schema,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    _idempotency.clear()
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: 1)

    mcp = FastMCP("test")
    task_tools.register(mcp)
    create = _get_tool(mcp, "task_create")

    result = create(
        project="sr",
        plan_scope="sr-plan",
        section_letter="A",
        title="rec test",
        type="feature",
        priority="medium",
        id_prefix="REC",
    )
    assert "recommended_skills" in result
    assert "task-standard" in result["recommended_skills"]
    assert len(result["recommended_skills"]) <= 3
