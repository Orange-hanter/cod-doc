"""ROAD-005/006 (RFC 36 §3.6): ``roadmap_get``, ``cod-doc roadmap show``, REST.

Контракты RFC 36 §3.7: C-03 (профили и reader), C-04 (одна форма JSON на
трёх поверхностях при одних ``asof``/``seed``), C-19 (ответ не называет дату
сроком). Один проект в реестре, поверхности зовутся по-настоящему: MCP — через
тул на свежем FastMCP, CLI — ``CliRunner``, REST — ``TestClient``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, TaskModel
from cod_doc.mcp.access_policy import READER_TOOLS
from cod_doc.mcp.profiles import keep_tool

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

SLUG = "roadproj"
ASOF = "2026-10-28"


def _cli(args: list[str]) -> str:
    result = CliRunner().invoke(main, args, env={"FORCE_COLOR": ""})
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def project(tmp_path: Path) -> Iterator[None]:
    """Проект с двумя планами, пятью открытыми и шестью закрытыми задачами."""
    root = tmp_path / SLUG
    root.mkdir()
    _cli(["project", "add", str(root), "--name", SLUG])
    for scope in ("first", "second"):
        _cli(["plan", "create", scope, "-p", SLUG, "--principle", f"{scope} principle"])
        _cli(["plan", "section", "create", scope, "A", "Main", "-p", SLUG])
    entry = Config.load().get_project(SLUG)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as s:
            sections = {
                scope: (plan_id, section_id)
                for scope, plan_id, section_id in s.execute(
                    select(PlanModel.scope, PlanModel.row_id, PlanSectionModel.row_id).join(
                        PlanSectionModel, PlanSectionModel.plan_id == PlanModel.row_id
                    )
                ).all()
            }
            project_id = s.scalar(select(PlanModel.project_id).limit(1))
            closed_at = datetime(2026, 10, 1, tzinfo=UTC)
            for i in range(11):
                scope = "first" if i % 2 else "second"
                done = i >= 5
                stamp = closed_at - timedelta(days=7 * (i - 5)) if done else closed_at
                s.add(
                    TaskModel(
                        project_id=project_id,
                        task_id=f"RD-{i:03d}",
                        plan_id=sections[scope][0],
                        section_id=sections[scope][1],
                        title=f"task {i}",
                        status="done" if done else "todo",
                        type="feature",
                        priority="medium",
                        created=stamp,
                        last_updated=stamp,
                        completed_at=stamp if done else None,
                    )
                )
    finally:
        engine.dispose()
    yield


def _mcp_roadmap(**kwargs: Any) -> dict[str, Any]:
    from cod_doc.mcp.tools import roadmap_tools

    mcp = FastMCP("test")
    roadmap_tools.register(mcp)
    tool = mcp._tool_manager._tools["roadmap_get"].fn
    out = tool(project=SLUG, **kwargs)
    assert isinstance(out, dict)
    return out


# --- C-03 -----------------------------------------------------------------------------


def test_roadmap_get_profiles_and_reader() -> None:
    assert keep_tool("roadmap_get", "full") is True
    assert keep_tool("roadmap_get", "standard") is True
    assert keep_tool("roadmap_get", "agent") is False
    assert keep_tool("roadmap_get", "minimal") is False
    assert "roadmap_get" in READER_TOOLS
    assert "plan_move" not in READER_TOOLS  # мутация


# --- C-04, C-19 ---------------------------------------------------------------------


@pytest.mark.usefixtures("project")
def test_three_surfaces_return_the_same_json() -> None:
    mcp_out = _mcp_roadmap(asof=ASOF, seed=3)
    cli_out = json.loads(
        _cli(["roadmap", "show", "-p", SLUG, "--asof", ASOF, "--seed", "3", "--json"])
    )

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get(f"/api/v1/projects/{SLUG}/roadmap", params={"asof": ASOF, "seed": 3})
    assert response.status_code == 200, response.text

    assert mcp_out == cli_out == response.json()
    assert [r["task_id"] for r in mcp_out["queue"]["rows"]] == [
        "RD-001",
        "RD-003",
        "RD-000",
        "RD-002",
        "RD-004",
    ]
    assert mcp_out["forecast"]["asof"] == ASOF
    assert all(r["p50"] is not None for r in mcp_out["queue"]["rows"])
    assert "not deadlines" in mcp_out["meaning"]


@pytest.mark.usefixtures("project")
def test_plan_filter_keeps_project_positions() -> None:
    whole = _mcp_roadmap(asof=ASOF)
    second = _mcp_roadmap(asof=ASOF, plan_scope="second")
    assert [r["plan_scope"] for r in second["queue"]["rows"]] == ["second"] * 3
    assert [r["position"] for r in second["queue"]["rows"]] == [2, 3, 4]
    assert [p["scope"] for p in second["queue"]["plans"]] == ["second"]
    assert second["forecast"]["warnings"] == whole["forecast"]["warnings"]


@pytest.mark.usefixtures("project")
def test_plan_move_reorders_the_roadmap() -> None:
    _cli(["plan", "move", "second", "-p", SLUG, "--before", "first"])
    out = _mcp_roadmap(asof=ASOF)
    assert [p["scope"] for p in out["queue"]["plans"]] == ["second", "first"]
    assert out["queue"]["rows"][0]["task_id"] == "RD-000"


@pytest.mark.usefixtures("project")
def test_cli_table_says_forecast_not_deadline() -> None:
    out = _cli(["roadmap", "show", "-p", SLUG, "--asof", ASOF])
    assert "не сроки" in out
    assert "RD-001" in out
    assert "срок:" not in out.lower()


@pytest.mark.usefixtures("project")
def test_cli_table_prints_titles_literally() -> None:
    """Заголовок с rich-разметкой печатается как есть: ячейка — Text, не markup."""
    entry = Config.load().get_project(SLUG)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as s:
            task = s.scalars(select(TaskModel).where(TaskModel.task_id == "RD-001")).one()
            task.title = "[/bold] скобки [b]как есть[/b]"
    finally:
        engine.dispose()
    out = _cli(["roadmap", "show", "-p", SLUG, "--asof", ASOF])
    assert "[/bold]" in out  # разметка не съедена (ширина CliRunner переносит строку)
