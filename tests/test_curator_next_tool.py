"""MCP ``curator_next``: флаги ``include_skill_bodies`` и ``skip_links`` (AFT-002)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP

from cod_doc.cli import main
from cod_doc.mcp.tools import curator_tools

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_CURATOR_SKILL_NAMES = ["orchestrator", "drift-handling", "ground-truth-reconcile", "doc-style"]


def _project(tmp_path: Path, name: str = "p") -> None:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    (root / "alpha.md").write_text(
        "---\ntype: standard\nstatus: active\n---\n# Alpha\n\n## Details\n\nSee [[doc:missing]].\n"
    )
    result = runner.invoke(main, ["import", "docs", name])
    assert result.exit_code == 0, result.output


def _tool() -> Callable[..., dict[str, Any]]:
    mcp = FastMCP("test")
    curator_tools.register(mcp)
    fn: Callable[..., dict[str, Any]] = mcp._tool_manager._tools["curator_next"].fn
    return fn


def test_curator_next_default_has_no_skill_bodies(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _project(tmp_path)

    payload = _tool()(project="p")

    skills = payload["navigation"]["applicable_skills"]
    assert [s["name"] for s in skills] == _CURATOR_SKILL_NAMES
    assert all("body" not in s for s in skills)


def test_curator_next_include_skill_bodies(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _project(tmp_path)

    payload = _tool()(project="p", include_skill_bodies=True)

    skills = payload["navigation"]["applicable_skills"]
    assert [s["name"] for s in skills] == _CURATOR_SKILL_NAMES
    assert all(s.get("body") for s in skills)


def test_curator_next_skip_links(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _project(tmp_path)
    tool = _tool()

    # Без флага: alpha.md держит [[doc:missing]] — раздел links собран и не пуст,
    # а признака «не собиралось» нет вовсе.
    full = tool(project="p")
    assert full["card"]["links"]
    assert "not_collected" not in full["meta"]

    skipped = tool(project="p", skip_links=True)
    assert skipped["card"]["links"] == []
    assert skipped["meta"]["not_collected"] == ["links"]
