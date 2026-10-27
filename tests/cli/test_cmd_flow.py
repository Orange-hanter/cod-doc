"""FLW-003: `cod-doc flow rebuild-metrics` — зеркало MCP flow_metrics_rebuild."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _invoke(args: list[str]) -> str:
    result = CliRunner().invoke(main, args, env={"FORCE_COLOR": ""})
    assert result.exit_code == 0, result.output
    return result.output


def test_rebuild_metrics_json_reports_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "fl"
    root.mkdir()
    _invoke(["project", "add", str(root), "--name", "fl"])
    _invoke(["plan", "create", "p", "-p", "fl", "--principle", "P"])
    _invoke(["plan", "section", "create", "p", "A", "Main", "-p", "fl"])
    _invoke(
        [
            "task",
            "create",
            "-p",
            "fl",
            "--plan",
            "p",
            "--section",
            "A",
            "--title",
            "One",
            "--id",
            "FL-001",
            "--type",
            "feature",
            "--priority",
            "medium",
        ]
    )
    _invoke(["task", "complete", "FL-001", "-p", "fl", "--commit", "abc1234"])

    dry = json.loads(_invoke(["flow", "rebuild-metrics", "-p", "fl", "--dry-run", "--json"]))
    first = json.loads(_invoke(["flow", "rebuild-metrics", "-p", "fl", "--json"]))
    second = json.loads(_invoke(["flow", "rebuild-metrics", "-p", "fl", "--json"]))

    assert dry["dry_run"] is True
    assert dry["tasks"] == 1
    assert first["tasks"] == 1
    assert first["without_events"] == 0
    assert (second["created"], second["updated"]) == (0, 0)
