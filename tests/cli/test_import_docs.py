"""CLI tests for ``cod-doc import docs --dry-run --limit`` (ADO-059, friction #8)."""

from __future__ import annotations

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


def _write_corpus(root: Path, count: int) -> None:
    for i in range(count):
        (root / f"doc{i:03d}.md").write_text(f"# Doc {i}\n\nBody {i}.")


def test_dry_run_default_limit_shows_preview_and_hint(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    root = _init_project(tmp_path)
    _write_corpus(root, 60)

    runner = CliRunner()
    result = runner.invoke(main, ["import", "docs", "p", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Файлы (61):" in result.output  # 60 + MASTER.md от project add
    assert result.output.count("•") == 50
    assert "ещё 11" in result.output
    assert "--limit 0" in result.output


def test_dry_run_limit_zero_lists_all(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _write_corpus(root, 60)

    runner = CliRunner()
    result = runner.invoke(main, ["import", "docs", "p", "--dry-run", "--limit", "0"])
    assert result.exit_code == 0, result.output
    assert result.output.count("•") == 61
    assert "ещё" not in result.output


def test_dry_run_limit_n_lists_exactly_n(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _write_corpus(root, 60)

    runner = CliRunner()
    result = runner.invoke(main, ["import", "docs", "p", "--dry-run", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert result.output.count("•") == 5
    assert "ещё 56" in result.output
