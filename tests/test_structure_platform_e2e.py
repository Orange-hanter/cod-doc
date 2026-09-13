"""Cross-repo e2e: ai-reviewer producer JSON ingested by cod-doc."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cod_doc.cli.cmd_ctx import ctx
from cod_doc.cli.cmd_ingest import ingest
from cod_doc.cli.structure import structure
from tests.test_structure_cli import _bootstrap

PRODUCER_ROOT = Path(os.environ.get("AI_REVIEWER_ROOT", "/workspace"))
NODE = shutil.which("node") or str(Path.home() / ".nvm/versions/node/v22.22.2/bin/node")

GRAPH = {
    "nodes": [
        {
            "id": "api",
            "label": "validate()",
            "file_type": "code",
            "source_file": "src/auth.ts",
            "source_location": "L10",
            "_callable": True,
            "community": 1,
        },
        {
            "id": "caller1",
            "label": "login()",
            "file_type": "code",
            "source_file": "src/login.ts",
            "source_location": "L4",
            "_callable": True,
            "community": 2,
        },
        {
            "id": "test",
            "label": "accepts a valid token()",
            "file_type": "code",
            "source_file": "test/auth.test.ts",
            "source_location": "L3",
            "_callable": True,
            "community": 3,
        },
    ],
    "links": [
        {"source": "caller1", "target": "api", "relation": "calls", "confidence": "EXTRACTED"},
        {"source": "test", "target": "api", "relation": "calls", "confidence": "EXTRACTED"},
    ],
}

LCOV = """TN:
SF:src/auth.ts
FN:10,validate
FNDA:2,validate
DA:10,2
end_of_record
"""

HEAD = "abcdef1234567890"


def _producer_available() -> bool:
    return Path(NODE).exists() and (PRODUCER_ROOT / "bin" / "pr-review-structure.mjs").is_file()


@pytest.mark.skipif(not _producer_available(), reason="ai-reviewer producer not available")
def test_producer_snapshot_ingests_idempotently_and_requires_pin(tmp_path: Path) -> None:
    repo = tmp_path / "src-repo"
    (repo / "src").mkdir(parents=True)
    (repo / "test").mkdir()
    (repo / "test" / "auth.test.ts").write_text(
        "import { test } from 'node:test';\n"
        "test('accepts a valid token', () => { expect(validate('good')).toEqual(true); });\n",
        encoding="utf-8",
    )
    graph_path = repo / "graph.json"
    graph_path.write_text(json.dumps(GRAPH), encoding="utf-8")
    (repo / "graph-report.md").write_text(f"Built from commit: `{HEAD}`\n", encoding="utf-8")
    (repo / "lcov.info").write_text(LCOV, encoding="utf-8")
    out = repo / "structure-out"
    out.mkdir()

    produced = subprocess.run(
        [
            NODE,
            str(PRODUCER_ROOT / "bin" / "pr-review-structure.mjs"),
            "--repo-root",
            str(repo),
            "--graph",
            str(graph_path),
            "--graph-report",
            str(repo / "graph-report.md"),
            "--lcov",
            str(repo / "lcov.info"),
            "--head-sha",
            HEAD,
            "--branch",
            "main",
            "--facts-out",
            str(out / "structure-facts.json"),
            "--assessment-out",
            str(out / "structure-assessment.json"),
            "--markdown",
            str(out / "code-structure.md"),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(PRODUCER_ROOT),
    )
    assert produced.returncode == 0, produced.stderr + produced.stdout
    facts_path = out / "structure-facts.json"
    assessment_path = out / "structure-assessment.json"
    markdown = (out / "code-structure.md").read_text(encoding="utf-8")
    assert "source_of_truth: false" in markdown
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    assert facts["kind"] == "structure_facts"
    assert assessment["kind"] == "structure_assessment"
    assert assessment["factsFingerprint"] == facts["fingerprint"]
    assert facts["provenance"]["headSha"] == HEAD

    cfg, entry = _bootstrap(tmp_path)
    project_root = Path(entry.path)
    before = {path.name for path in project_root.rglob("*") if path.is_file()}
    runner = CliRunner()
    first = runner.invoke(
        ingest,
        [
            "structure",
            "-p",
            "struct-proj",
            "--facts",
            str(facts_path),
            "--assessment",
            str(assessment_path),
            "--trust-tier",
            "trusted_local",
            "--json",
        ],
        obj={"config": cfg},
    )
    assert first.exit_code == 0, first.output
    payload = json.loads(first.output)
    assert payload["publishedCurrent"] is True
    assert payload["idempotent"] is False
    assert payload["bytes"] > 0
    assert "durationMs" in payload

    second = runner.invoke(
        ingest,
        [
            "structure",
            "-p",
            "struct-proj",
            "--facts",
            str(facts_path),
            "--assessment",
            str(assessment_path),
            "--trust-tier",
            "trusted_local",
            "--json",
        ],
        obj={"config": cfg},
    )
    assert second.exit_code == 0, second.output
    again = json.loads(second.output)
    assert again["idempotent"] is True
    assert again["snapshot"]["fingerprint"] == payload["snapshot"]["fingerprint"]

    unpinned = runner.invoke(ctx, ["structure", "-p", "struct-proj"], obj={"config": cfg})
    assert unpinned.exit_code != 0

    pinned = runner.invoke(
        ctx,
        ["structure", "-p", "struct-proj", "--head-sha", HEAD],
        obj={"config": cfg},
    )
    assert pinned.exit_code == 0, pinned.output
    context = json.loads(pinned.output)
    assert context["pinned"]["headSha"] == HEAD
    assert context["bytes"] <= 32 * 1024

    replay = runner.invoke(
        structure,
        [
            "replay",
            "-p",
            "struct-proj",
            "--snapshot-id",
            str(payload["snapshot"]["snapshotId"]),
            "--json",
        ],
        obj={"config": cfg},
    )
    assert replay.exit_code == 0, replay.output
    replayed = json.loads(replay.output)
    assert replayed["indexes"]["entities"] == payload["indexes"]["entities"]

    after = {path.name for path in project_root.rglob("*") if path.is_file()}
    assert "MASTER.md" not in after - before
    assert "code-structure.md" not in after - before
