"""FLW-001 (RFC 33 F3): одна смена статуса через MCP — одно событие.

``task_service.update_status`` и ``task_service.complete`` сами пишут
``task.status_changed`` / ``task.completed``. MCP-обёртки поверх этого писали
второе событие того же вида (у ``task.status_changed`` — без ``old_status``):
лента и ``activity_summary`` считали переход дважды, а метрики потока
(FLW-003) видели переход без исходного статуса.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import plan_service

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session, sessionmaker

AUTHOR = "agent:dakh/claude-code"


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


@pytest.fixture
def tools(
    monkeypatch: pytest.MonkeyPatch, sf: sessionmaker[Session]
) -> dict[str, Callable[..., Any]]:
    from cod_doc.mcp.tools import task_tools

    now = datetime.now(UTC)
    with transactional(sf) as s:
        proj = ProjectModel(slug="ev", title="ev", root_path="/tmp/ev", config_json={})
        proj.created = now
        proj.updated = now
        s.add(proj)
        s.flush()
        pid = proj.row_id
        plan_service.create_plan(s, project_id=pid, scope="p", principle=None, author=AUTHOR)
        plan_service.create_section(
            s, project_id=pid, plan_scope="p", letter="A", title="A", author=AUTHOR
        )
    monkeypatch.setattr(task_tools, "session_factory", lambda project: (sf, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    fns = {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}
    fns["task_create"](
        project="ev",
        plan_scope="p",
        section_letter="A",
        title="Event subject",
        task_id="EV-001",
        type="feature",
        priority="medium",
        author=AUTHOR,
    )
    return fns


def _events(sf: sessionmaker[Session], kind: str) -> list[dict[str, Any]]:
    with transactional(sf) as s:
        return [
            dict(payload or {})
            for payload in s.scalars(
                select(ActivityEventModel.payload).where(
                    ActivityEventModel.kind == kind, ActivityEventModel.scope_id == "EV-001"
                )
            )
        ]


def test_status_change_via_mcp_writes_one_event_with_old_status(
    tools: dict[str, Callable[..., Any]], sf: sessionmaker[Session]
) -> None:
    tools["task_update_status"](
        project="ev", task_id="EV-001", new_status="blocked", author=AUTHOR, reason="waits"
    )
    events = _events(sf, "task.status_changed")
    assert len(events) == 1, events
    assert events[0].get("old_status") == "todo"
    assert events[0].get("new_status") == "blocked"


def test_complete_via_mcp_writes_one_completed_event(
    tools: dict[str, Callable[..., Any]], sf: sessionmaker[Session]
) -> None:
    tools["task_complete"](project="ev", task_id="EV-001", author=AUTHOR, commit_sha="abc1234")
    assert len(_events(sf, "task.completed")) == 1
