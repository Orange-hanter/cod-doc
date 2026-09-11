"""CLI ``plan create`` / ``section-create`` / ``sections``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _init_project(tmp_path: Path, name: str = "p") -> None:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output


def test_plan_create_then_task_create(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    runner = CliRunner()

    created = runner.invoke(
        main,
        [
            "plan",
            "create",
            "-p",
            "p",
            "--scope",
            "hardening-2026-09",
            "--section",
            "A:One",
        ],
    )
    assert created.exit_code == 0, created.output
    assert "hardening-2026-09" in created.output

    task = runner.invoke(
        main,
        [
            "task",
            "create",
            "-p",
            "p",
            "--plan",
            "hardening-2026-09",
            "--section",
            "A",
            "--title",
            "Do the thing",
            "--type",
            "feature",
            "--priority",
            "medium",
            "--prefix",
            "HRD",
        ],
    )
    assert task.exit_code == 0, task.output


def test_plan_create_duplicate_scope_exits_1(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    runner = CliRunner()
    args = ["plan", "create", "-p", "p", "--scope", "dup-plan"]
    first = runner.invoke(main, args)
    assert first.exit_code == 0, first.output
    second = runner.invoke(main, args)
    assert second.exit_code == 1
    assert "already exists" in second.output


def test_plan_sections_json(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    runner = CliRunner()
    created = runner.invoke(
        main,
        ["plan", "create", "-p", "p", "--scope", "sec-plan", "--section", "A:Alpha"],
    )
    assert created.exit_code == 0, created.output

    listed = runner.invoke(main, ["plan", "sections", "sec-plan", "-p", "p", "--json"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert rows[0]["letter"] == "A"
    assert rows[0]["title"] == "Alpha"
    assert rows[0]["task_count"] == 0


def test_plan_section_create_appends(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    runner = CliRunner()
    created = runner.invoke(main, ["plan", "create", "-p", "p", "--scope", "grow"])
    assert created.exit_code == 0, created.output

    added = runner.invoke(
        main,
        [
            "plan",
            "section-create",
            "grow",
            "-p",
            "p",
            "--letter",
            "B",
            "--title",
            "Beta",
        ],
    )
    assert added.exit_code == 0, added.output
    assert "Beta" in added.output
