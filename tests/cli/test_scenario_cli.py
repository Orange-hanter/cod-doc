"""TSC-006: `cod-doc scenario` — surface parity with the service layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path

DOC_KEY = "docs/system/capabilities/plan-management"


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _new_scenario(runner: CliRunner, *extra: str):  # type: ignore[no-untyped-def]
    return runner.invoke(
        main,
        [
            "scenario",
            "new",
            "-p",
            "p",
            "--title",
            "Plan progress recomputes after a task completes",
            "--kind",
            "happy_path",
            "--doc-key",
            DOC_KEY,
            "--precondition",
            "A plan with one open task exists.",
            "--step",
            "Complete the task",
            "--step",
            "Read the plan progress",
            "--expected",
            "plan_progress reports one task done.",
            *extra,
        ],
    )


# --------------------------------------------------------------------------- #
# help surface                                                                 #
# --------------------------------------------------------------------------- #


def test_help_lists_every_command() -> None:
    result = CliRunner().invoke(main, ["scenario", "--help"])
    assert result.exit_code == 0
    for cmd in (
        "new",
        "list",
        "show",
        "update",
        "retire",
        "steps",
        "link",
        "unlink",
        "export",
        "coverage",
    ):
        assert cmd in result.output, f"scenario {cmd} missing from --help"


def test_new_help_exposes_the_authoring_fields() -> None:
    result = CliRunner().invoke(main, ["scenario", "new", "--help"])
    assert result.exit_code == 0
    for opt in (
        "--kind",
        "--doc-key",
        "--section-anchor",
        "--precondition",
        "--step",
        "--expected",
    ):
        assert opt in result.output, f"{opt} missing"


def test_coverage_verdicts_are_not_offered_as_statuses() -> None:
    """The CLI must not let anyone type an RFC 24 §9 verdict as a claim status."""
    result = CliRunner().invoke(main, ["scenario", "update", "--help"])
    assert result.exit_code == 0
    for verdict in ("covered", "partial", "unverifiable"):
        assert verdict not in result.output


# --------------------------------------------------------------------------- #
# round trip                                                                   #
# --------------------------------------------------------------------------- #


def test_new_then_list_then_show(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    runner = CliRunner()

    created = _new_scenario(runner)
    assert created.exit_code == 0, created.output
    assert "SCN-001" in created.output

    listed = runner.invoke(main, ["scenario", "list", "-p", "p", "--json"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert rows[0]["scenario_id"] == "SCN-001"
    assert rows[0]["group_key"] == "plan-management"

    shown = runner.invoke(main, ["scenario", "show", "SCN-001", "-p", "p", "--json"])
    assert shown.exit_code == 0, shown.output
    payload = json.loads(shown.output)
    assert payload["steps"] == ["Complete the task", "Read the plan progress"]
    assert payload["kind"] == "happy_path"


def test_update_and_retire(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    runner = CliRunner()
    assert _new_scenario(runner).exit_code == 0

    updated = runner.invoke(
        main, ["scenario", "update", "SCN-001", "-p", "p", "--title", "Renamed"]
    )
    assert updated.exit_code == 0, updated.output

    retired = runner.invoke(main, ["scenario", "retire", "SCN-001", "-p", "p"])
    assert retired.exit_code == 0, retired.output

    shown = runner.invoke(main, ["scenario", "show", "SCN-001", "-p", "p", "--json"])
    payload = json.loads(shown.output)
    assert payload["title"] == "Renamed"
    assert payload["status"] == "retired"


def test_steps_replace_all(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    runner = CliRunner()
    assert _new_scenario(runner).exit_code == 0

    result = runner.invoke(
        main, ["scenario", "steps", "SCN-001", "-p", "p", "--step", "Only this one"]
    )
    assert result.exit_code == 0, result.output

    shown = runner.invoke(main, ["scenario", "show", "SCN-001", "-p", "p", "--json"])
    assert json.loads(shown.output)["steps"] == ["Only this one"]


def test_link_and_unlink(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    runner = CliRunner()
    assert _new_scenario(runner).exit_code == 0

    args = [
        "SCN-001",
        "-p",
        "p",
        "--to-kind",
        "criterion",
        "--to-ref",
        "US-013#2",
        "--relation",
        "verifies",
    ]
    assert runner.invoke(main, ["scenario", "link", *args]).exit_code == 0
    shown = runner.invoke(main, ["scenario", "show", "SCN-001", "-p", "p", "--json"])
    assert json.loads(shown.output)["links"][0]["ref"] == "US-013#2"

    assert runner.invoke(main, ["scenario", "unlink", *args]).exit_code == 0
    shown = runner.invoke(main, ["scenario", "show", "SCN-001", "-p", "p", "--json"])
    assert json.loads(shown.output)["links"] == []


# --------------------------------------------------------------------------- #
# errors                                                                       #
# --------------------------------------------------------------------------- #


def test_unknown_scenario_exits_nonzero(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    result = CliRunner().invoke(main, ["scenario", "show", "SCN-404", "-p", "p"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_bad_kind_is_rejected_by_click(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "scenario",
            "new",
            "-p",
            "p",
            "--title",
            "T",
            "--kind",
            "smoke",
            "--group",
            "g",
            "--precondition",
            "x",
            "--step",
            "y",
            "--expected",
            "z",
        ],
    )
    assert result.exit_code != 0
    assert "smoke" in result.output


def test_coverage_reports_missing_kinds(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    runner = CliRunner()
    assert _new_scenario(runner).exit_code == 0

    result = runner.invoke(main, ["scenario", "coverage", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows[0]["missing_kinds"] == ["error_path"]
