"""CLI tests for ``cod-doc ctx docs|drift|search`` (SYM-008 / ADO-057)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _import_corpus(root: Path, name: str = "p") -> None:
    runner = CliRunner()
    (root / "alpha.md").write_text(
        "---\ntype: standard\nstatus: active\n---\n# Alpha\n\n## Details\n\nSee [[doc:missing]].\n"
    )
    (root / "beta.md").write_text(
        "---\ntype: standard\nstatus: active\n---\n# Beta\n\nBeta body content.\n"
    )
    result = runner.invoke(main, ["import", "docs", name])
    assert result.exit_code == 0, result.output


def test_ctx_docs_json_valid(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert "docs" in data
    assert "links_at_risk" in data
    assert "token_estimate" in data
    assert isinstance(data["token_estimate"], int)
    assert data["token_estimate"] > 0
    assert len(data["docs"]) >= 2
    doc_keys = {d["doc_key"] for d in data["docs"]}
    assert "alpha" in doc_keys
    assert "beta" in doc_keys
    # Сломанная ссылка [[doc:missing]] должна быть найдена.
    broken = [link for link in data["links_at_risk"] if link["source_doc_key"] == "alpha"]
    assert any("missing" in (link["broken_reason"] or "") for link in broken)


def test_ctx_docs_paths_filter(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--paths", "*alpha*", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert len(data["docs"]) == 1
    assert data["docs"][0]["doc_key"] == "alpha"


def test_ctx_docs_budget_tokens(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "docs", "-p", "p", "--budget-tokens", "0", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["docs"] == []
    assert data["token_estimate"] == 0


def test_ctx_drift_json_valid(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "drift", "-p", "p", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["project"] == "p"
    assert "total_docs" in data
    assert "problem_count" in data
    assert "counts" in data
    assert "issues" in data
    assert data["total_docs"] >= 2


def test_ctx_search_json_valid(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "search", "-p", "p", "alpha", "--json"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    assert data["query"] == "alpha"
    assert "total" in data
    assert "by_kind" in data
    assert data["total"] > 0
    assert any(data["by_kind"][kind] for kind in data["by_kind"])


def test_ctx_dry_read_writes_nothing(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    for args in [
        ["ctx", "docs", "-p", "p", "--json"],
        ["ctx", "drift", "-p", "p", "--json"],
        ["ctx", "search", "-p", "p", "beta", "--json"],
    ]:
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output

    # Повторный импорт должен пропустить все документы: ctx ничего не изменил.
    result = runner.invoke(main, ["import", "docs", "p", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Skipped (already in DB):" in result.output


def test_ctx_help_in_russian(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["ctx", "--help"])
    assert result.exit_code == 0, result.output
    assert "Контекст" in result.output
    assert "docs" in result.output
    assert "drift" in result.output
    assert "search" in result.output
