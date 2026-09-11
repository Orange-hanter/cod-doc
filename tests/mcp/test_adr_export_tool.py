"""MCP ``adr_export`` writes ADR-*.md and is idempotent on a second call."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.mcp.tools import adr_tools
from cod_doc.services import adr_service

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch
    from sqlalchemy.orm import Session


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_adr_export_writes_then_noop(
    engine_with_schema, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="Use FTS5")

    monkeypatch.setattr(adr_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    adr_tools.register(mcp)
    fn = _get_tool(mcp, "adr_export")
    out_dir = str(tmp_path / "docs" / "adr")

    first = fn(project="p", out_dir=out_dir)
    assert first["count"] == 1
    assert any(name.endswith("ADR-001.md") for name in first["written"])
    assert (tmp_path / "docs" / "adr" / "ADR-001.md").is_file()

    second = fn(project="p", out_dir=out_dir)
    assert second["written"] == []
    assert second["count"] == 0
