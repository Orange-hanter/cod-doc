"""AFT-001: every surface that emits a project drift row emits the same row.

ADO-216 (#102) put the row into `ProjectDriftItem.as_payload()`, but MCP
`ctx_drift` kept a copy of the `doc_drift_all` body, and CLI `cod-doc ctx drift`
built the row by hand without `metadata_mismatch` and `orphan_sections`. The
corpus is the one from `test_drift_metadata_mismatch_json.py`: a document whose
content hashes agree while the frontmatter `status` disagrees with the DB.
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING, Any

from click.testing import CliRunner

from cod_doc.cli.cmd_ctx import ctx
from tests.test_drift_metadata_mismatch_json import SLUG, _bootstrap_repo, _mcp_tool

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

ROW_KEYS = {
    "doc_key",
    "path",
    "status",
    "projection_hash",
    "db_content_hash",
    "file_hash",
    "metadata_mismatch",
    "orphan_sections",
}


def test_ctx_drift_equals_doc_drift_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, repo = _bootstrap_repo(tmp_path)
    ctx_drift = _mcp_tool(monkeypatch, repo, "ctx_drift")
    doc_drift_all = _mcp_tool(monkeypatch, repo, "doc_drift_all")

    assert ctx_drift(project=SLUG) == doc_drift_all(project=SLUG)
    assert ctx_drift(project=SLUG, limit=1) == doc_drift_all(project=SLUG, limit=1)


def test_ctx_drift_delegates_to_doc_drift_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, repo = _bootstrap_repo(tmp_path)
    source = inspect.getsource(_mcp_tool(monkeypatch, repo, "ctx_drift"))

    assert "doc_drift_all(" in source
    assert "detect_project_drift" not in source


def test_cli_ctx_drift_json_rows_carry_metadata_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    cfg, _ = _bootstrap_repo(tmp_path)

    result = CliRunner().invoke(ctx, ["drift", "-p", SLUG, "--json"], obj={"config": cfg})

    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    assert payload["issues"]
    for row in payload["issues"]:
        assert set(row) == ROW_KEYS
    (issue,) = [row for row in payload["issues"] if row["doc_key"] == "master"]
    assert issue["status"] == "in_sync"
    assert issue["metadata_mismatch"] == ["status"]
