"""Tests for the Orakul ai-review poller script."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest


def _fake_gh_script(run_ids: list[int], artifacts: dict[int, list[str]]) -> str:
    runs_lines = "\n".join(str(rid) for rid in run_ids)
    runs_literal = repr(runs_lines)
    artifacts_map: dict[str, list[str]] = {str(rid): names for rid, names in artifacts.items()}
    return f"""#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
if args[:4] == ["repo", "view", "--json", "nameWithOwner"]:
    print("Mozarella/Orakul")
elif args[:2] == ["run", "list"]:
    print({runs_literal})
elif args[0] == "api":
    run_id = int(pathlib.Path(args[1]).parts[-2])
    artifacts = {artifacts_map!r}
    names = artifacts.get(str(run_id), [])
    if "-q" in args:
        print("\\n".join(names))
    else:
        print(json.dumps({{"artifacts": [{{"name": n}} for n in names]}}))
else:
    print("unexpected gh call:", args, file=sys.stderr)
    sys.exit(1)
"""


def _fake_cod_doc_script(log_path: Path) -> str:
    return f"""#!/usr/bin/env python3
import sys
with open({str(log_path)!r}, "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
"""


def _write_script(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _run_poller(
    script: Path,
    *,
    repo_dir: Path,
    state_file: Path,
    bin_dir: Path,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    args = [
        str(script),
        "--repo",
        str(repo_dir),
        "--project",
        "p",
        "--state",
        str(state_file),
        "--limit",
        "10",
    ]
    if dry_run:
        args.append("--dry-run")
    return subprocess.run(args, env=env, capture_output=True, text=True)


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return bin_dir


@pytest.fixture
def poller_script() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "scripts" / "ingest-orakul-reviews.sh"


def test_poller_ingests_new_pr_review_artifacts(
    tmp_path: Path,
    fake_bin: Path,
    poller_script: Path,
    isolated_cod_doc_home: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    state_file = tmp_path / "state"
    cod_doc_log = tmp_path / "cod-doc.log"

    _write_script(
        fake_bin / "gh",
        _fake_gh_script(
            run_ids=[111, 222], artifacts={111: ["pr-review-export-42"], 222: ["other"]}
        ),
    )
    _write_script(fake_bin / "cod-doc", _fake_cod_doc_script(cod_doc_log))

    result = _run_poller(
        poller_script,
        repo_dir=repo_dir,
        state_file=state_file,
        bin_dir=fake_bin,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Ingesting pr-review-export-42" in result.stdout
    calls = cod_doc_log.read_text().strip().splitlines()
    assert calls == ["ingest ai_review --project p --from-pr 42"]
    assert state_file.read_text().strip() == "111/42"


def test_poller_is_idempotent(
    tmp_path: Path,
    fake_bin: Path,
    poller_script: Path,
    isolated_cod_doc_home: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    state_file = tmp_path / "state"
    cod_doc_log = tmp_path / "cod-doc.log"

    _write_script(
        fake_bin / "gh",
        _fake_gh_script(run_ids=[111], artifacts={111: ["pr-review-export-42"]}),
    )
    _write_script(fake_bin / "cod-doc", _fake_cod_doc_script(cod_doc_log))

    _run_poller(poller_script, repo_dir=repo_dir, state_file=state_file, bin_dir=fake_bin)
    assert state_file.read_text().strip() == "111/42"

    result = _run_poller(
        poller_script,
        repo_dir=repo_dir,
        state_file=state_file,
        bin_dir=fake_bin,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "Skip already processed: 111/42" in result.stdout
    assert cod_doc_log.read_text().strip().splitlines() == [
        "ingest ai_review --project p --from-pr 42"
    ]


def test_poller_dry_run_does_not_ingest_or_update_state(
    tmp_path: Path,
    fake_bin: Path,
    poller_script: Path,
    isolated_cod_doc_home: Any,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    state_file = tmp_path / "state"
    cod_doc_log = tmp_path / "cod-doc.log"

    _write_script(
        fake_bin / "gh",
        _fake_gh_script(run_ids=[111], artifacts={111: ["pr-review-export-42"]}),
    )
    _write_script(fake_bin / "cod-doc", _fake_cod_doc_script(cod_doc_log))

    result = _run_poller(
        poller_script,
        repo_dir=repo_dir,
        state_file=state_file,
        bin_dir=fake_bin,
        dry_run=True,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Would ingest pr-review-export-42" in result.stdout
    assert not cod_doc_log.exists() or cod_doc_log.read_text().strip() == ""
    assert state_file.read_text().strip() == ""
