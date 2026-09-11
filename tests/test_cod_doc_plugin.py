"""Contract tests for plugins/cod-doc — Claude Code + Cursor / Agent Plugins.

The plugin ships skills, commands, and hooks. It does not spawn MCP:
Cursor's Claude-plugin bridge cannot interpolate host variables, so both
`.mcp.json` and `mcp.json` keep an empty server map. Hosts get the binary
via `cod-doc connect install`.
"""

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from tests.test_server_profiles import EXPECTED_PROFILE_COUNTS

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "cod-doc"
PLUGIN_VERSION = "0.1.3"


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_empty_servers(path: Path) -> None:
    data = _read_json(path)
    servers = data["mcpServers"]
    assert isinstance(servers, dict)
    assert servers == {}
    blob = json.dumps(data)
    assert "${" not in blob
    assert '"command": "./' not in blob
    assert "bash" not in blob


def test_plugin_layout_exists() -> None:
    assert (PLUGIN_ROOT / "plugin.json").is_file()
    assert (PLUGIN_ROOT / "mcp.json").is_file()
    assert (PLUGIN_ROOT / ".mcp.json").is_file()
    assert (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").is_file()
    assert (PLUGIN_ROOT / ".cursor-plugin" / "plugin.json").is_file()
    assert (PLUGIN_ROOT / "scripts" / "mcp-launch.sh").is_file()
    assert (PLUGIN_ROOT / "scripts" / "cod-doc-env.sh").is_file()


@pytest.mark.parametrize(
    "relpath",
    [
        "plugin.json",
        ".claude-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
    ],
)
def test_manifest_versions_match(relpath: str) -> None:
    data = _read_json(PLUGIN_ROOT / relpath)
    assert data["name"] == "cod-doc"
    assert data["version"] == PLUGIN_VERSION


def test_agent_plugins_manifest_declares_schema() -> None:
    data = _read_json(PLUGIN_ROOT / "plugin.json")
    schema = data.get("$schema")
    assert isinstance(schema, str)
    assert schema.endswith("/plugin.schema.json")


def test_cursor_mcp_json_does_not_spawn() -> None:
    """Cursor reads plugins/cod-doc/mcp.json. An empty map is the contract."""
    _assert_empty_servers(PLUGIN_ROOT / "mcp.json")


def test_claude_mcp_json_does_not_spawn() -> None:
    """Cursor also loads the Claude plugin `.mcp.json`. Empty map, no spawn."""
    _assert_empty_servers(PLUGIN_ROOT / ".mcp.json")


@pytest.mark.parametrize(
    "script",
    ["mcp-launch.sh", "cod-doc-env.sh", "md-drift-reminder.sh", "session-brief.sh"],
)
def test_plugin_scripts_are_executable_and_parse(script: str) -> None:
    path = PLUGIN_ROOT / "scripts" / script
    assert path.stat().st_mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_plugin_readme_profile_counts_match_server() -> None:
    text = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    for name, count in EXPECTED_PROFILE_COUNTS.items():
        needle = f"`{name}` ({count})"
        assert needle in text, f"README.md should mention {needle}"
    assert f"standard` profile ({EXPECTED_PROFILE_COUNTS['standard']} tools)" in text
