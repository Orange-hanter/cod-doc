"""Smoke tests for the native COD-DOC MCP server."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

import cod_doc.config as config_module
from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project


@pytest.fixture
def mcp_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ProjectEntry, Path]:
    config_dir = tmp_path / ".cod-doc-home"
    config_file = config_dir / "config.yaml"
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    repo = tmp_path / "mcp-repo"
    repo.mkdir()
    entry = ProjectEntry(name="mcp-test", path=str(repo))
    Project(entry).init()
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://example.com")
    cfg.projects = [entry.model_dump()]
    cfg.save()
    return entry, config_dir


def _open_stdio_client(config_dir: Path):
    params = StdioServerParameters(
        command=sys.executable,
        # Tests assert presence of legacy tools (list_projects, add_task,
        # get_master, …) which are hidden under the cycle-4 'standard'
        # default. Pin --profile full for back-compat coverage.
        args=["-m", "cod_doc.mcp.server", "--transport", "stdio", "--profile", "full"],
        env={**os.environ, "COD_DOC_HOME": str(config_dir)},
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    return stdio_client(params)


@pytest.mark.anyio
async def test_mcp_lists_tools(mcp_project: tuple[ProjectEntry, Path]) -> None:
    _, config_dir = mcp_project
    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()

    tool_names = [tool.name for tool in tools.tools]
    # legacy tools
    assert "list_projects" in tool_names
    assert "add_task" in tool_names
    assert "get_master" in tool_names
    # COD-032: DB-backed tool groups
    assert "task_list" in tool_names
    assert "task_create" in tool_names
    assert "task_complete" in tool_names
    assert "doc_list" in tool_names
    assert "doc_get" in tool_names
    assert "doc_export" in tool_names
    assert "plan_progress" in tool_names
    assert "plan_ready" in tool_names
    assert "plan_critical_path" in tool_names
    # PCA-901: plan-create surface
    assert "plan_create" in tool_names
    assert "plan_section_create" in tool_names
    # PCA-010: heartbeat-context surface
    assert "task_heartbeat_context" in tool_names
    # PCA-032: run-id audit trail
    assert "run_list" in tool_names
    assert "run_get" in tool_names
    # PCA-033: run-revert dry-run
    assert "run_revert" in tool_names
    # PCA-003: agent-skill catalog
    assert "skill_list" in tool_names
    assert "skill_get" in tool_names
    assert "story_list" in tool_names
    assert "story_create" in tool_names
    assert "story_coverage" in tool_names
    assert "link_list" in tool_names
    assert "link_verify" in tool_names
    assert "revision_list" in tool_names
    assert "revision_revert" in tool_names


@pytest.mark.anyio
async def test_mcp_add_task_and_get_master(mcp_project: tuple[ProjectEntry, Path]) -> None:
    entry, config_dir = mcp_project
    async with (
        _open_stdio_client(config_dir) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        task_result = await session.call_tool(
            "add_task",
            {"project_name": entry.name, "title": "Проверить MCP workflow", "priority": 2},
        )
        master_result = await session.call_tool("get_master", {"project_name": entry.name})

    task_text = task_result.content[0].text
    master_text = master_result.content[0].text
    assert "Проверить MCP workflow" in task_text
    assert "MASTER" in master_text or "Navigator" in master_text
