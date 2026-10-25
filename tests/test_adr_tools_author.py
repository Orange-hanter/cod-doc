"""MCP ADR-тулы пишут под автором вызывающего, а не под зашитым ``agent``.

Под Bearer-токеном ``activity_service.emit`` требует, чтобы ``author`` совпадал
с владельцем токена (``agent:<user>/<client>``). Пока тулы подставляли
``author="agent"``, любая правка ADR через MCP падала с SYNC-202, и принятый
ADR нельзя было поправить ни одной поверхностью.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.infra.models.activity import ActivityEventModel
from cod_doc.services import adr_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

AUTHOR = "agent:dakh/claude-code"


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
        adr_service.create(s, project_id=pid, title="Черновик", adr_id="ADR-001")
        adr_service.create(s, project_id=pid, title="Принятое", adr_id="ADR-002", status="accepted")
        adr_service.create(s, project_id=pid, title="Замена", adr_id="ADR-003")

    monkeypatch.setattr(adr_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(adr_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    adr_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


def _last_actor(factory: sessionmaker[Session]) -> str | None:
    with transactional(factory) as s:
        row = s.scalars(
            select(ActivityEventModel).order_by(ActivityEventModel.row_id.desc()).limit(1)
        ).one()
        return row.actor_id


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("adr_update", {"adr_id": "ADR-001", "title": "Черновик 2"}),
        ("adr_sync_body", {"adr_id": "ADR-002", "context": "Уточнённый контекст"}),
        ("adr_add_diagram", {"adr_id": "ADR-001", "mermaid": "flowchart LR\n  a --> b"}),
        ("adr_relate", {"from_adr_id": "ADR-003", "to_adr_id": "ADR-002", "kind": "amends"}),
        ("adr_supersede", {"superseding_adr_id": "ADR-003", "superseded_adr_id": "ADR-001"}),
        ("adr_deprecate", {"adr_id": "ADR-002"}),
        ("adr_topic_create", {"name": "Данные"}),
    ],
)
def test_mutating_tool_records_caller_author(
    tools: dict[str, Callable[..., Any]],
    factory: sessionmaker[Session],
    tool: str,
    kwargs: dict[str, Any],
) -> None:
    tools[tool](project="p", author=AUTHOR, **kwargs)
    assert _last_actor(factory) == AUTHOR


def test_author_defaults_to_agent(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    tools["adr_update"](project="p", adr_id="ADR-001", title="Без автора")
    assert _last_actor(factory) == "agent"


def test_every_mutating_adr_tool_accepts_author(tools: dict[str, Callable[..., Any]]) -> None:
    import inspect

    read_only = {"adr_get", "adr_list", "adr_graph", "adr_topic_list"}
    missing = [
        name
        for name, fn in tools.items()
        if name not in read_only and "author" not in inspect.signature(fn).parameters
    ]
    assert missing == []
