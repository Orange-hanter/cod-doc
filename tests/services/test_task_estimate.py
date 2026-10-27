"""ROAD-009 (RFC 36 §3.2.3): оценка задачи в человеко-часах; контракт C-20.

Сервис ``task_service.set_estimate``, MCP ``task_set_estimate``, CLI
``cod-doc task estimate``. Эталоны — прямые SELECT по ``task``, ``revision``,
``activity_event``.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP
from sqlalchemy import func, select

from cod_doc.cli import main
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, RevisionModel, TaskModel
from cod_doc.services import task_service
from tests.services.test_checkout_service import _seed_task

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

AUTHOR = "agent:dakh/claude-code"


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


@pytest.fixture
def pid(sf: sessionmaker[Session]) -> int:
    with transactional(sf) as s:
        return _seed_task(s, status="todo")


def _state(sf: sessionmaker[Session]) -> tuple[float | None, int, int]:
    """(оценка, ревизии op=estimate, события task.estimate_changed)."""
    with transactional(sf) as s:
        task = s.scalars(select(TaskModel).where(TaskModel.task_id == "CO-001")).one()
        diffs = s.scalars(
            select(RevisionModel.diff).where(
                RevisionModel.entity_kind == "task", RevisionModel.entity_id == task.row_id
            )
        ).all()
        events = s.scalar(
            select(func.count(ActivityEventModel.row_id)).where(
                ActivityEventModel.kind == "task.estimate_changed"
            )
        )
        return (
            task.estimate_person_hours,
            sum(json.loads(d).get("op") == "estimate" for d in diffs),
            int(events or 0),
        )


def _set(sf: sessionmaker[Session], pid: int, hours: float | None) -> float | None:
    with transactional(sf) as s:
        t = task_service.set_estimate(
            s, project_id=pid, task_id="CO-001", person_hours=hours, author=AUTHOR
        )
        return t.estimate_person_hours


def test_set_writes_revision_and_event(sf: sessionmaker[Session], pid: int) -> None:
    assert _set(sf, pid, 6.0) == 6.0
    assert _state(sf) == (6.0, 1, 1)


def test_same_value_is_noop(sf: sessionmaker[Session], pid: int) -> None:
    _set(sf, pid, 6.0)
    _set(sf, pid, 6.0)
    assert _state(sf) == (6.0, 1, 1)


def test_none_clears(sf: sessionmaker[Session], pid: int) -> None:
    _set(sf, pid, 6.0)
    assert _set(sf, pid, None) is None
    assert _state(sf) == (None, 2, 2)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan, True, "5"])
def test_non_positive_is_refused_without_write(
    sf: sessionmaker[Session], pid: int, bad: float
) -> None:
    """C-20: ≤ 0 (и не число) — ошибка без записи."""
    with pytest.raises(ValueError, match="positive"):
        _set(sf, pid, bad)
    assert _state(sf) == (None, 0, 0)


def test_mcp_task_set_estimate(
    monkeypatch: pytest.MonkeyPatch, sf: sessionmaker[Session], pid: int
) -> None:
    from cod_doc.mcp.tools import task_tools

    monkeypatch.setattr(task_tools, "session_factory", lambda project: (sf, None))
    monkeypatch.setattr(task_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    task_tools.register(mcp)
    tool: Callable[..., Any] = mcp._tool_manager._tools["task_set_estimate"].fn

    out = tool(project="co", task_id="CO-001", person_hours=4.5, author=AUTHOR)
    assert out["estimate_person_hours"] == 4.5
    with pytest.raises(ValueError, match="positive"):
        tool(project="co", task_id="CO-001", person_hours=-2, author=AUTHOR)
    assert _state(sf) == (4.5, 1, 1)


def test_cli_task_estimate(tmp_path: Path) -> None:
    runner = CliRunner()
    env = {"FORCE_COLOR": ""}

    def ok(args: list[str]) -> str:
        result = runner.invoke(main, args, env=env)
        assert result.exit_code == 0, result.output
        return result.output

    root = tmp_path / "es"
    root.mkdir()
    ok(["project", "add", str(root), "--name", "es"])
    ok(["plan", "create", "p", "-p", "es", "--principle", "P"])
    ok(["plan", "section", "create", "p", "A", "Main", "-p", "es"])
    ok(
        [
            "task",
            "create",
            "-p",
            "es",
            "--plan",
            "p",
            "--section",
            "A",
            "--title",
            "One",
            "--id",
            "ES-001",
            "--type",
            "feature",
            "--priority",
            "medium",
        ]
    )

    set_out = json.loads(ok(["task", "estimate", "ES-001", "8", "-p", "es", "--json"]))
    cleared = json.loads(ok(["task", "estimate", "ES-001", "--clear", "-p", "es", "--json"]))
    refused = runner.invoke(main, ["task", "estimate", "ES-001", "0", "-p", "es"], env=env)
    both = runner.invoke(main, ["task", "estimate", "ES-001", "3", "--clear", "-p", "es"], env=env)

    assert set_out["estimate_person_hours"] == 8.0
    assert cleared["estimate_person_hours"] is None
    assert refused.exit_code != 0
    assert "positive" in refused.output
    assert both.exit_code != 0
