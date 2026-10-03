"""ARG-001/ARG-002: MCP adr_relate / adr_unrelate / adr_get / adr_update.

Тулы зовутся на свежем FastMCP с подменённым session_factory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import adr_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=url)
    engine = make_engine(url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def tools(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> dict[str, Callable[..., Any]]:
    from cod_doc.mcp.tools import adr_tools

    now = datetime.now(UTC)
    with transactional(factory) as s:
        proj = ProjectModel(slug="p", title="p", root_path="/tmp/p", created=now, updated=now)
        s.add(proj)
        s.flush()
        pid = proj.row_id
        adr_service.create(s, project_id=pid, title="Реплики", adr_id="ADR-016")
        adr_service.create(s, project_id=pid, title="Идентичность", adr_id="ADR-017")

    monkeypatch.setattr(adr_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(adr_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    adr_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


def test_relate_then_get_and_graph(tools: dict[str, Callable[..., Any]]) -> None:
    out = tools["adr_relate"](
        project="p", from_adr_id="ADR-017", to_adr_id="ADR-016", kind="depends_on", reason="r"
    )
    assert (out["from"], out["to"], out["kind"], out["reason"]) == (
        "ADR-017",
        "ADR-016",
        "depends_on",
        "r",
    )
    got = tools["adr_get"](project="p", adr_id="ADR-016")
    assert [(r["adr_id"], r["kind"]) for r in got["relations"]["incoming"]] == [
        ("ADR-017", "depends_on")
    ]
    graph = tools["adr_graph"](project="p")
    assert graph["relations"] == [
        {"from": "ADR-017", "to": "ADR-016", "kind": "depends_on", "reason": "r"}
    ]


def test_relate_errors_surface_as_value_error(tools: dict[str, Callable[..., Any]]) -> None:
    with pytest.raises(ValueError, match="ADR-999"):
        tools["adr_relate"](project="p", from_adr_id="ADR-017", to_adr_id="ADR-999", kind="amends")
    with pytest.raises(ValueError, match="invalid relation kind"):
        tools["adr_relate"](project="p", from_adr_id="ADR-017", to_adr_id="ADR-016", kind="x")


def test_unrelate_missing_is_value_error(tools: dict[str, Callable[..., Any]]) -> None:
    with pytest.raises(ValueError, match="no amends relation"):
        tools["adr_unrelate"](
            project="p", from_adr_id="ADR-017", to_adr_id="ADR-016", kind="amends"
        )
    tools["adr_relate"](project="p", from_adr_id="ADR-017", to_adr_id="ADR-016", kind="amends")
    assert tools["adr_unrelate"](
        project="p", from_adr_id="ADR-017", to_adr_id="ADR-016", kind="amends"
    )["removed"]


def test_update_accept_stamps_date(tools: dict[str, Callable[..., Any]]) -> None:
    out = tools["adr_update"](project="p", adr_id="ADR-016", status="accepted")
    assert out["decided_at"] == datetime.now(UTC).date().isoformat()


def test_sync_body_clear_decided_at(tools: dict[str, Callable[..., Any]]) -> None:
    tools["adr_sync_body"](project="p", adr_id="ADR-017", decided_at="2026-05-17")
    out = tools["adr_sync_body"](project="p", adr_id="ADR-017", clear_decided_at=True)
    assert out["decided_at"] is None
