"""COD-052 / STB-014: MCP surface for plan_freeze + doc_accept (the freeze+accept flow)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    Plan,
    PlanSection,
    Sensitivity,
)
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import doc_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="fz", title="fz", root_path="/tmp/fz", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    plan = PlanRepository(session).add(
        Plan(project_id=proj.row_id, scope="fz-plan", principle="test-first")
    )
    plan.created = now
    plan.last_updated = now
    session.flush()
    PlanSectionRepository(session).add(
        PlanSection(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    )
    return proj.row_id


def _register(monkeypatch, module, factory, proj_id: int) -> FastMCP:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(module, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(module, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    module.register(mcp)
    return mcp


def test_plan_freeze_tool_creates_frozen_snapshot(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import plan_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        proj_id = _seed(s)

    freeze = _get_tool(_register(monkeypatch, plan_tools, factory, proj_id), "plan_freeze")
    out = freeze(project="fz", plan_scope="fz-plan", author="human:test")

    assert out["frozen_doc_key"].startswith("frozen/fz-plan/")
    assert out["document_id"] is not None

    with transactional(factory) as s:
        d = doc_service.get(s, proj_id, out["frozen_doc_key"])
        assert d is not None
        assert d.type == DocumentType.EXECUTION_LOG
        assert d.status == DocumentStatus.ACTIVE


def test_plan_freeze_tool_unknown_plan_raises(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import plan_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        proj_id = _seed(s)

    freeze = _get_tool(_register(monkeypatch, plan_tools, factory, proj_id), "plan_freeze")
    with pytest.raises(ValueError, match="not found"):
        freeze(project="fz", plan_scope="no-such-plan", author="human:test")


def test_doc_accept_tool_promotes_draft_to_active(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import doc_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        proj_id = _seed(s)
        doc_service.create(
            s,
            project_id=proj_id,
            doc_key="draft/x",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="X",
            author="human:test",
            owner="human:test",
            sensitivity=Sensitivity.INTERNAL,
            preamble="body",
        )

    accept = _get_tool(_register(monkeypatch, doc_tools, factory, proj_id), "doc_accept")
    out = accept(project="fz", doc_key="draft/x", author="human:test")
    assert out["status"] == "active"

    with pytest.raises(ValueError, match="not found"):
        accept(project="fz", doc_key="nope", author="human:test")
