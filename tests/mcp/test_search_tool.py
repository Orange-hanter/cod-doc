"""MCP ``search`` — FTS hit by title (reindex via the service, not MCP)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.mcp.tools import search_tools
from cod_doc.services import search_service, task_service

if TYPE_CHECKING:
    from pytest import MonkeyPatch
    from sqlalchemy.orm import Session


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session: Session) -> tuple[int, int, int]:
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


def test_search_finds_task_by_title(engine_with_schema, monkeypatch: MonkeyPatch) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        task_service.create(
            session,
            project_id=pid,
            plan_id=plid,
            section_id=sid,
            task_id="SRP-020",
            title="Implement checkout endpoint",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="t",
        )
        search_service.reindex_all(session, project_id=pid)

    monkeypatch.setattr(search_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    search_tools.register(mcp)
    result = _get_tool(mcp, "search")(project="p", query="checkout")

    assert result["total"] >= 1
    assert result["by_kind"]["task"][0]["ref"] == "SRP-020"
