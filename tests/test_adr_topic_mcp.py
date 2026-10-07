"""ARG-008: MCP-тулы полок ADR на свежем FastMCP с подменённым session_factory."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
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
        adr_service.create(
            s, project_id=pid, title="PostgreSQL", adr_id="ADR-005", status="accepted"
        )

        s.add(ProjectModel(slug="other", title="o", root_path="/tmp/o", created=now, updated=now))

    # Подменяется только фабрика сессий; проект резолвится настоящим
    # require_project_id по слагу, так что скоуп проверяется по-честному.
    monkeypatch.setattr(adr_tools, "session_factory", lambda project: (factory, None))
    mcp = FastMCP("test")
    adr_tools.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


def test_topic_tools_roundtrip(tools: dict[str, Callable[..., Any]]) -> None:
    created = tools["adr_topic_create"](project="p", name="Хранение", includes="SQLite")
    assert created == {
        "name": "Хранение",
        "includes": "SQLite",
        "excludes": "",
        "position": 0,
        "adr_count": 0,
    }
    tools["adr_topic_create"](project="p", name="Агент")
    tools["adr_topic_move"](project="p", name="Агент", position=0)
    assert tools["adr_set_topic"](project="p", adr_id="ADR-005", topic="Хранение") == {
        "adr_id": "ADR-005",
        "topic": "Хранение",
    }
    listing = tools["adr_topic_list"](project="p")
    assert [(t["name"], t["position"], t["adr_count"]) for t in listing] == [
        ("Агент", 0, 0),
        ("Хранение", 1, 1),
    ]
    assert tools["adr_list"](project="p")[0]["topic"] == "Хранение"
    tools["adr_topic_update"](project="p", name="Хранение", excludes="Эмбеддинги")
    assert tools["adr_topic_delete"](project="p", name="Хранение") == {
        "name": "Хранение",
        "deleted": True,
        "unshelved": 1,
    }
    assert tools["adr_get"](project="p", adr_id="ADR-005")["topic"] is None


def test_set_topic_unknown_is_value_error(tools: dict[str, Callable[..., Any]]) -> None:
    with pytest.raises(ValueError, match="topic 'Нет такой' not found"):
        tools["adr_set_topic"](project="p", adr_id="ADR-005", topic="Нет такой")
    with pytest.raises(ValueError, match="ADR-404"):
        tools["adr_set_topic"](project="p", adr_id="ADR-404", topic=None)
    with pytest.raises(ValueError, match="topic 'Нет такой' not found"):
        tools["adr_topic_delete"](project="p", name="Нет такой")


def test_topic_tools_emit_activity_events(
    tools: dict[str, Callable[..., Any]], factory: sessionmaker[Session]
) -> None:
    tools["adr_topic_create"](project="p", name="Хранение")
    tools["adr_topic_update"](project="p", name="Хранение", includes="SQLite")
    tools["adr_set_topic"](project="p", adr_id="ADR-005", topic="Хранение")
    tools["adr_topic_delete"](project="p", name="Хранение")
    with transactional(factory) as s:
        kinds = list(
            s.execute(
                select(ActivityEventModel.kind)
                .where(ActivityEventModel.kind.like("adr.topic%"))
                .order_by(ActivityEventModel.row_id)
            ).scalars()
        )
        actors = set(
            s.execute(
                select(ActivityEventModel.actor_kind, ActivityEventModel.actor_id).where(
                    ActivityEventModel.kind.like("adr.topic%")
                )
            ).all()
        )
    # MCP пишет от агента: actor_kind выведен единственной точкой из author="agent".
    assert actors == {("agent", "agent")}
    assert kinds == [
        "adr.topic_created",
        "adr.topic_updated",
        "adr.topic_set",
        "adr.topic_set",
        "adr.topic_deleted",
    ]


def test_topic_tools_are_project_scoped(tools: dict[str, Callable[..., Any]]) -> None:
    tools["adr_topic_create"](project="p", name="Хранение")
    with pytest.raises(ValueError, match="ADR-005"):
        tools["adr_set_topic"](project="other", adr_id="ADR-005", topic=None)
    with pytest.raises(ValueError, match="topic 'Хранение' not found"):
        tools["adr_topic_delete"](project="other", name="Хранение")
    assert tools["adr_topic_list"](project="other") == []
    with pytest.raises(ValueError, match="not in DB"):
        tools["adr_topic_list"](project="nope")
    assert [t["name"] for t in tools["adr_topic_list"](project="p")] == ["Хранение"]


def test_set_topic_keeps_accepted_body_frozen(tools: dict[str, Callable[..., Any]]) -> None:
    """Смена полки через MCP не размораживает тело принятого решения."""
    tools["adr_topic_create"](project="p", name="Хранение")
    tools["adr_set_topic"](project="p", adr_id="ADR-005", topic="Хранение")
    with pytest.raises(ValueError, match=r"(?i)accepted|immutable|frozen"):
        tools["adr_update"](project="p", adr_id="ADR-005", title="Другое")
    assert tools["adr_get"](project="p", adr_id="ADR-005")["title"] == "PostgreSQL"
