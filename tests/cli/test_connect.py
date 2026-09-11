"""CLI contract for ``cod-doc connect``."""

from __future__ import annotations

import json
import os
import stat
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path

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


def test_which_prints_resolved_binary(tmp_path: Path) -> None:
    binary = _exec(tmp_path / ".venv" / "bin" / "cod-doc-mcp", "#!/bin/sh\nexit 0\n")
    result = CliRunner().invoke(
        main,
        [
            "connect",
            "which",
            "--home",
            str(tmp_path / "home"),
            "--project-root",
            str(tmp_path),
            "--no-search-system",
        ],
    )
    assert result.exit_code == 0, result.output
    assert str(binary.resolve()) in result.output


def test_doctor_missing_binary_exits_nonzero(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "connect",
            "doctor",
            "--home",
            str(tmp_path),
            "--project-root",
            str(tmp_path),
            "--no-search-system",
            "--no-handshake",
        ],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_install_cursor_merges_without_clobber(tmp_path: Path) -> None:
    binary = _exec(tmp_path / "cod-doc-mcp", _HANDSHAKE_SCRIPT)
    cursor_cfg = tmp_path / ".cursor" / "mcp.json"
    cursor_cfg.parent.mkdir(parents=True)
    cursor_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "/usr/bin/true", "args": []},
                }
            }
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "COD_DOC_MCP_BIN": str(binary)}
    result = CliRunner().invoke(
        main,
        [
            "connect",
            "install",
            "--client",
            "cursor",
            "--home",
            str(tmp_path),
            "--project-root",
            str(tmp_path),
            "--no-search-system",
            "--no-shim",
        ],
        env=env,
    )
    assert result.exit_code == 0, result.output
    data = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"]["command"] == "/usr/bin/true"
    entry = data["mcpServers"]["cod-doc"]
    assert entry["command"] == str(binary.resolve())
    assert "${" not in json.dumps(entry)
    assert not entry["command"].startswith("./")

    again = CliRunner().invoke(
        main,
        [
            "connect",
            "install",
            "--client",
            "cursor",
            "--home",
            str(tmp_path),
            "--project-root",
            str(tmp_path),
            "--no-search-system",
            "--no-shim",
        ],
        env=env,
    )
    assert again.exit_code == 0, again.output
    data2 = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert list(data2["mcpServers"]) == ["other", "cod-doc"]
