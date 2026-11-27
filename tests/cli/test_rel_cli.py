"""`cod-doc rel` (RFC 39 §3.7, REL-010)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner(tmp_path: Path, isolated_cod_doc_home: Path) -> CliRunner:
    root = tmp_path / "p"
    root.mkdir()
    r = CliRunner()
    assert r.invoke(main, ["project", "add", str(root), "--name", "p"]).exit_code == 0
    for sid in ("US-007", "US-064"):
        res = r.invoke(
            main,
            [
                "story",
                "create",
                "-p",
                "p",
                "--id",
                sid,
                "--persona",
                "dev",
                "--narrative",
                "n",
                "--priority",
                "medium",
            ],
        )
        assert res.exit_code == 0, res.output
    return r


def _run(r: CliRunner, *args: str) -> str:
    res = r.invoke(main, ["rel", *args])
    assert res.exit_code == 0, res.output
    return res.output


def test_add_ls_rm_roundtrip_json(runner: CliRunner) -> None:
    out = json.loads(
        _run(runner, "add", "story:US-064", "see_also", "story:US-007", "-p", "p", "--json")
    )
    assert out["type"] == "relates_to"
    assert out["created"] is True
    rows = json.loads(_run(runner, "ls", "story:US-007", "-p", "p", "--json"))
    assert [(r["label"], r["other"], r["source"]) for r in rows] == [
        ("relates_to", "story:US-064", "relation")
    ]
    assert json.loads(_run(runner, "ls", "story:US-007", "-p", "p", "--out", "--json")) == rows
    assert json.loads(_run(runner, "ls", "story:US-007", "-p", "p", "--in", "--json")) == []
    assert (
        json.loads(_run(runner, "ls", "story:US-007", "-p", "p", "-t", "verifies", "--json")) == []
    )
    assert json.loads(
        _run(runner, "rm", "story:US-007", "relates_to", "story:US-064", "-p", "p", "--json")
    ) == {"removed": True}


def test_error_is_one_message_with_hint_and_exit_1(runner: CliRunner) -> None:
    res = runner.invoke(
        main, ["rel", "add", "story:US-064", "depend_on", "story:US-007", "-p", "p"]
    )
    assert res.exit_code == 1
    assert "Похоже: depends_on" in res.output
    assert "Traceback" not in res.output


def test_types_define_deprecate_verify(runner: CliRunner) -> None:
    types = json.loads(
        _run(runner, "types", "-p", "p", "--from", "story", "--to", "story", "--json")
    )
    assert "depends_on" in {t["name"] for t in types}
    _run(
        runner, "type", "define", "mitigates", "-p", "p", "--inverse", "mitigated_by",
        "--from", "story", "--to", "story",
    )  # fmt: skip
    assert "deprecated" in _run(runner, "type", "deprecate", "mitigates", "-p", "p")
    assert json.loads(_run(runner, "verify", "-p", "p", "--json"))["checked"] == 0
