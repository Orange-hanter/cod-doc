"""Contract tests for resolving and wiring the local MCP stdio binary."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from cod_doc.services.mcp_host import (
    EXPECTED_SERVER_INFO_NAME,
    HandshakeError,
    McpBinaryNotFoundError,
    assert_host_safe,
    doctor,
    handshake,
    install_client,
    merge_json_server,
    resolve_mcp_binary,
    stdio_server,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_MCP = REPO_ROOT / ".venv" / "bin" / "cod-doc-mcp"

_HANDSHAKE_SCRIPT = """\
#!/usr/bin/env python3
import json
import sys
import time

line = sys.stdin.readline()
req = json.loads(line)
sys.stdout.write(json.dumps({
    "jsonrpc": "2.0",
    "id": req.get("id"),
    "result": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "serverInfo": {"name": "COD-DOC", "version": "test"},
    },
}) + "\\n")
sys.stdout.flush()
time.sleep(30)
"""


def _exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_resolve_prefers_cod_doc_mcp_bin(tmp_path: Path) -> None:
    binary = _exec(tmp_path / "override-mcp", "#!/bin/sh\nexit 0\n")
    found = resolve_mcp_binary(
        project_root=tmp_path,
        home=tmp_path,
        env={"COD_DOC_MCP_BIN": str(binary)},
        include_system=False,
    )
    assert found == binary.resolve()


def test_resolve_uses_project_venv(tmp_path: Path) -> None:
    binary = _exec(tmp_path / ".venv" / "bin" / "cod-doc-mcp", "#!/bin/sh\nexit 0\n")
    found = resolve_mcp_binary(
        project_root=tmp_path,
        home=tmp_path / "home",
        env={},
        include_system=False,
    )
    assert found == binary.resolve()


def test_resolve_bad_override_does_not_fall_through(tmp_path: Path) -> None:
    _exec(tmp_path / ".venv" / "bin" / "cod-doc-mcp", "#!/bin/sh\nexit 0\n")
    with pytest.raises(McpBinaryNotFoundError, match="COD_DOC_MCP_BIN"):
        resolve_mcp_binary(
            project_root=tmp_path,
            home=tmp_path,
            env={"COD_DOC_MCP_BIN": str(tmp_path / "missing")},
            include_system=False,
        )


def test_resolve_missing_without_system(tmp_path: Path) -> None:
    with pytest.raises(McpBinaryNotFoundError):
        resolve_mcp_binary(
            project_root=tmp_path,
            home=tmp_path,
            env={},
            include_system=False,
        )


def test_assert_host_safe_rejects_interpolation_and_relative() -> None:
    with pytest.raises(ValueError, match=r"\$\{"):
        assert_host_safe({"command": "${CLAUDE_PLUGIN_ROOT}/scripts/mcp-launch.sh"})
    with pytest.raises(ValueError, match="absolute"):
        assert_host_safe({"command": "./scripts/mcp-launch.sh"})
    with pytest.raises(ValueError, match="absolute"):
        assert_host_safe({"command": "cod-doc-mcp"})


def test_cursor_generator_is_host_safe(tmp_path: Path) -> None:
    binary = _exec(tmp_path / "cod-doc-mcp", "#!/bin/sh\nexit 0\n")
    path = tmp_path / ".cursor" / "mcp.json"
    other = {"command": "/usr/bin/true", "args": []}
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"other": other}}), encoding="utf-8")
    result = install_client(
        "cursor",
        binary=binary,
        home=tmp_path,
        project_root=tmp_path,
    )
    data = json.loads(result.path.read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "${" not in blob
    assert '"command": "./' not in blob
    servers = data["mcpServers"]
    assert servers["other"] == other
    entry = servers["cod-doc"]
    assert_host_safe(entry)
    assert entry["command"] == str(binary.resolve())
    assert entry["args"] == ["--profile", "standard"]
    assert Path(entry["command"]).is_absolute()


@pytest.mark.parametrize(
    ("client", "relpath", "key"),
    [
        ("claude-code", ".claude.json", "mcpServers"),
        ("vscode", ".vscode/mcp.json", "servers"),
        ("claude-desktop", None, "mcpServers"),
    ],
)
def test_json_generators_are_host_safe(
    tmp_path: Path,
    client: str,
    relpath: str | None,
    key: str,
) -> None:
    binary = _exec(tmp_path / "cod-doc-mcp", "#!/bin/sh\nexit 0\n")
    result = install_client(
        client,
        binary=binary,
        home=tmp_path,
        project_root=tmp_path,
    )
    data = json.loads(result.path.read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "${" not in blob
    assert '"command": "./' not in blob
    entry = data[key]["cod-doc"]
    assert_host_safe(entry)
    if relpath is not None:
        assert result.path == tmp_path / relpath


def test_codex_generator_is_host_safe(tmp_path: Path) -> None:
    binary = _exec(tmp_path / "cod-doc-mcp", "#!/bin/sh\nexit 0\n")
    extra = tmp_path / ".codex" / "config.toml"
    extra.parent.mkdir(parents=True)
    extra.write_text('[model_providers.openai]\nname = "OpenAI"\n', encoding="utf-8")
    result = install_client(
        "codex",
        binary=binary,
        home=tmp_path,
        project_root=tmp_path,
    )
    text = result.path.read_text(encoding="utf-8")
    assert "${" not in text
    assert 'command = "./' not in text
    assert "[model_providers.openai]" in text
    assert "[mcp_servers.cod-doc]" in text
    assert str(binary.resolve()) in text


def test_stdio_server_record_has_no_interpolation(tmp_path: Path) -> None:
    binary = _exec(tmp_path / "cod-doc-mcp", "#!/bin/sh\nexit 0\n")
    entry = stdio_server(binary).as_dict()
    assert_host_safe(entry)
    merge_json_server(tmp_path / "mcp.json", servers_key="mcpServers", entry=entry)


def test_handshake_fake_binary(tmp_path: Path) -> None:
    binary = _exec(tmp_path / "cod-doc-mcp", _HANDSHAKE_SCRIPT)
    result = handshake(binary, timeout_sec=5)
    assert result.server_name == EXPECTED_SERVER_INFO_NAME


def test_handshake_rejects_wrong_name(tmp_path: Path) -> None:
    script = _HANDSHAKE_SCRIPT.replace("COD-DOC", "other")
    binary = _exec(tmp_path / "cod-doc-mcp", script)
    with pytest.raises(HandshakeError, match=r"serverInfo\.name"):
        handshake(binary, timeout_sec=5)


def test_handshake_against_venv_binary() -> None:
    if not VENV_MCP.is_file():
        pytest.skip("no local .venv/bin/cod-doc-mcp")
    result = handshake(VENV_MCP)
    assert result.server_name == EXPECTED_SERVER_INFO_NAME


def test_doctor_missing_binary(tmp_path: Path) -> None:
    report = doctor(
        project_root=tmp_path,
        home=tmp_path,
        env={},
        include_system=False,
    )
    assert report.binary is None
    assert report.handshake_ok is False
    assert report.handshake_error is not None
