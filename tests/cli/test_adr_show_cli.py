"""ADO-230: `cod-doc adr show` отдаёт обратные ссылки — паритет с MCP adr_get."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", "p"])
    assert result.exit_code == 0, result.output
    return root


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["adr", *args])
    return result.exit_code, result.output


def test_show_json_carries_referenced_by(root: Path) -> None:
    assert _run("new", "-p", "p", "-t", "Base", "--adr-id", "ADR-001")[0] == 0
    code, out = _run(
        "new", "-p", "p", "-t", "Next", "--adr-id", "ADR-002", "--context", "Строится на ADR-001."
    )
    assert code == 0, out

    code, out = _run("show", "-p", "p", "ADR-001", "--json")
    assert code == 0, out
    refs = json.loads(out)["referenced_by"]
    assert [a["adr_id"] for a in refs["adrs"]] == ["ADR-002"]

    code, out = _run("show", "-p", "p", "ADR-001")
    assert code == 0, out
    assert "Referenced by (1)" in out
