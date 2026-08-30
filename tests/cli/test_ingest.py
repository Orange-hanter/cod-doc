"""CLI tests for ``cod-doc ingest`` (SYM-006B)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from click.testing import CliRunner
from sqlalchemy import select
from sqlalchemy.orm import Session

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import make_engine
from cod_doc.infra.models import ActivityEventModel, FindingModel, FindingSourceRunModel

if TYPE_CHECKING:
    import pytest


def _ai_payload(*, head_sha: str = "abc123", pr: int = 42) -> dict[str, Any]:
    return {
        "version": 1,
        "headSha": head_sha,
        "reviewMode": "standard",
        "pr": {"number": pr},
        "findings": [
            {
                "title": "Issue one",
                "severity": "major",
                "file": "src/x.py",
                "line": 10,
                "fp": "fp-1",
                "model": "claude",
            }
        ],
        "blockers": [],
    }


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _db_counts(project_name: str) -> tuple[int, int]:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    engine = make_engine(f"sqlite:///{entry.cod_doc_dir / 'state.db'}")
    session = Session(engine)
    try:
        findings = session.execute(select(FindingModel)).scalars().all()
        runs = session.execute(select(FindingSourceRunModel)).scalars().all()
        return len(findings), len(runs)
    finally:
        session.close()
        engine.dispose()


def test_ingest_dry_run_writes_nothing(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    infile = tmp_path / "export.json"
    infile.write_text(json.dumps(_ai_payload()), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--input", str(infile), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "Dry-run" in result.output

    fcount, rcount = _db_counts("p")
    assert fcount == 0
    assert rcount == 0


def test_repeated_ingest_dedups_and_increments_times_seen(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path, "p")
    infile = tmp_path / "export.json"
    infile.write_text(json.dumps(_ai_payload()), encoding="utf-8")

    runner = CliRunner()
    result1 = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--input", str(infile), "--json"],
    )
    assert result1.exit_code == 0, result1.output
    data1 = json.loads(result1.output)
    assert data1["created"] == 1
    assert data1["updated"] == 0

    result2 = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--input", str(infile), "--json"],
    )
    assert result2.exit_code == 0, result2.output
    data2 = json.loads(result2.output)
    assert data2["created"] == 0
    assert data2["updated"] == 1

    fcount, rcount = _db_counts("p")
    assert fcount == 1
    assert rcount == 2


def test_ingest_from_pr_with_monkeypatched_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_cod_doc_home: Path,
) -> None:
    _init_project(tmp_path, "p")
    fixture = tmp_path / "pr-export.json"
    fixture.write_text(json.dumps(_ai_payload()), encoding="utf-8")

    def _fake_download(pr: int, dest: Path, repo: Path | None = None) -> None:
        shutil.copy(fixture, dest / f"pr-review-export-{pr}.json")

    monkeypatch.setattr("cod_doc.cli.cmd_ingest._download_pr_artifact", _fake_download)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--from-pr", "42", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["created"] == 1
    assert data["updated"] == 0
    assert "pr42" in data["source_run_id"]

    result2 = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--from-pr", "42", "--json"],
    )
    assert result2.exit_code == 0, result2.output
    data2 = json.loads(result2.output)
    assert data2["created"] == 0
    assert data2["updated"] == 1


def test_ingest_from_pr_repo_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_cod_doc_home: Path,
) -> None:
    _init_project(tmp_path, "p")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["cwd"] = kwargs.get("cwd")
        dest = Path(cmd[cmd.index("--dir") + 1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "pr-review-export-42.json").write_text(json.dumps(_ai_payload()), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("cod_doc.cli.cmd_ingest.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "ingest",
            "ai_review",
            "--project",
            "p",
            "--from-pr",
            "42",
            "--repo",
            str(repo_path),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["cwd"] == str(repo_path)


def test_ingest_from_pr_gh_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_cod_doc_home: Path,
) -> None:
    _init_project(tmp_path, "p")

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("gh")

    monkeypatch.setattr("cod_doc.cli.cmd_ingest.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--from-pr", "42"],
    )
    assert result.exit_code != 0
    assert "`gh` CLI not found" in result.output


def test_ingest_from_pr_gh_download_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_cod_doc_home: Path,
) -> None:
    _init_project(tmp_path, "p")

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, ["gh"], stderr="artifact not found")

    monkeypatch.setattr("cod_doc.cli.cmd_ingest.subprocess.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--from-pr", "42"],
    )
    assert result.exit_code != 0
    assert "Failed to download artifact" in result.output


def test_ingest_stdin_default_input(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--json"],
        input=json.dumps(_ai_payload()),
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["created"] == 1


def test_ingest_unknown_adapter_exits_with_error(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path, "p")
    runner = CliRunner()
    result = runner.invoke(main, ["ingest", "no_such_adapter", "--project", "p", "--input", "-"])
    assert result.exit_code != 0


def _activity_events(project_name: str) -> list[ActivityEventModel]:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    engine = make_engine(f"sqlite:///{entry.cod_doc_dir / 'state.db'}")
    session = Session(engine)
    try:
        return list(session.execute(select(ActivityEventModel)).scalars().all())
    finally:
        session.close()
        engine.dispose()


def test_ingest_emits_activity_event(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    infile = tmp_path / "export.json"
    infile.write_text(json.dumps(_ai_payload()), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--input", str(infile)],
    )
    assert result.exit_code == 0, result.output

    events = _activity_events("p")
    assert len(events) == 1
    event = events[0]
    assert event.kind == "finding.ingested"
    assert event.actor_kind == "cli"
    assert event.payload["adapter"] == "ai_review"
    assert event.payload["created"] == 1
    assert event.payload["updated"] == 0
    assert event.scope_kind == "source_run"
    assert event.scope_id is not None


def test_ingest_dry_run_does_not_emit_activity_event(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path, "p")
    infile = tmp_path / "export.json"
    infile.write_text(json.dumps(_ai_payload()), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["ingest", "ai_review", "--project", "p", "--input", str(infile), "--dry-run"],
    )
    assert result.exit_code == 0, result.output

    events = _activity_events("p")
    assert len(events) == 0
