"""ACU-006: `cod-doc ctx sweep|sync` и MCP `curator_sweep|curator_sync`.

Поверхности тонкие: координаты прогона собирает сервис по записи реестра.
Проверяется то, что у поверхностей своё: одинаковый отчёт, сухой режим по
умолчанию, запрет `--sync` без `--apply`, профиль `agent` без новых тулов.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP

from cod_doc.cli import main
from cod_doc.mcp.profiles import AGENT_TOOLS
from cod_doc.mcp.tools import curator_tools

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_FM = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"


def _project(tmp_path: Path, name: str = "p") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "alpha.md").write_text(f"{_FM}# Alpha\n\n## Details\n\nBody.\n", encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(main, ["project", "add", str(root), "--name", name]).exit_code == 0
    assert runner.invoke(main, ["import", "docs", name]).exit_code == 0
    with (root / "alpha.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка на диске.\n")
    return root


def _tool(name: str) -> Callable[..., dict[str, Any]]:
    mcp = FastMCP("test")
    curator_tools.register(mcp)
    fn: Callable[..., dict[str, Any]] = mcp._tool_manager._tools[name].fn
    return fn


def _cli_sweep(*args: str) -> dict[str, Any]:
    result = CliRunner().invoke(main, ["ctx", "sweep", "-p", "p", "--json", *args])
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    return payload


def test_cli_and_mcp_give_the_same_dry_run_report(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _project(tmp_path)

    cli = _cli_sweep()
    tool = _tool("curator_sweep")(project="p")

    assert cli == tool
    assert cli["applied"] is False
    assert cli["applied_count"] == 0


def test_dry_run_is_the_default_and_writes_nothing(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _project(tmp_path)

    first = _tool("curator_sweep")(project="p")
    second = _tool("curator_sweep")(project="p")

    planned = [a for a in first["repair"]["actions"] if not a["skipped"]]
    assert planned, "правка на диске обязана попасть в план"
    assert first == second, "сухой прогон ничего не менял — второй видит то же"


def test_apply_writes_and_a_repeat_has_nothing_left(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _project(tmp_path)

    applied = _cli_sweep("--apply")
    again = _cli_sweep("--apply")

    assert applied["applied_count"] >= 1
    assert again["applied_count"] == 0


def test_cli_refuses_sync_without_apply(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _project(tmp_path)

    result = CliRunner().invoke(main, ["ctx", "sweep", "-p", "p", "--sync"])

    assert result.exit_code == 2
    assert "--apply" in result.output


def test_mcp_ignores_sync_without_apply(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """Агенту не отказывают исключением, но наружу без `apply` ничего не уходит."""
    _project(tmp_path)

    payload = _tool("curator_sweep")(project="p", sync=True)

    assert payload["sync"] is None


def test_agent_profile_stays_without_the_new_tools() -> None:
    assert "curator_sweep" not in AGENT_TOOLS
    assert "curator_sync" not in AGENT_TOOLS
    assert len(AGENT_TOOLS) == 6
