"""Smoke tests for the native COD-DOC MCP server."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project


@pytest.fixture
def mcp_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ProjectEntry, Path]:
    config_dir = tmp_path / ".cod-doc-home"
    # ADO-068: путь резолвится в момент вызова — достаточно COD_DOC_HOME.
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("COD_DOC_HOME", str(config_dir))

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
        command=str(Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"),
        # Tests assert presence of the kept legacy agent tools (run_agent_once,
        # …) which are hidden under the cycle-4 'standard' default. Pin
        # --profile full for back-compat coverage.
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
    # legacy agent tools kept after STB-002 (YAML CRUD removed)
    assert "run_agent_once" in tool_names
    # COD-032: DB-backed tool groups
    assert "task_list" in tool_names
    assert "task_create" in tool_names
    assert "task_complete" in tool_names
    # ADO-025: dependency-edge removal surface
    assert "task_remove_dependency" in tool_names
    assert "doc_list" in tool_names
    assert "doc_get" in tool_names
    assert "doc_export" in tool_names
    # ADO-022: the only way an agent can get past the fidelity guard
    assert "doc_backfill_projection" in tool_names
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
    # SYM-006D / RFC 22: findings + ctx aliases (standard/full profiles)
    assert "finding_list" in tool_names
    assert "finding_get" in tool_names
    assert "finding_promote" in tool_names
    assert "finding_dismiss" in tool_names
    assert "ctx_docs" in tool_names
    assert "ctx_drift" in tool_names


# STB-002 (2026-06-08): removed test_mcp_add_task_and_get_master — it exercised
# the legacy YAML add_task + get_master tools, both deleted now that the DB is
# the source of truth. DB-backed task creation is covered by
# tests/test_mcp_task_create_field_passthrough.py and
# tests/integration/test_agent_profile_mcp.py.
