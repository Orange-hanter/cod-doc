"""CLI tests for ``cod-doc search`` (OBI-040 / CUR-010)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import text

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional

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
    (root / "alpha.md").write_text("---\ntype: standard\nstatus: active\n---\n# Alpha\n\nBody.\n")
    result = runner.invoke(main, ["import", "docs", name])
    assert result.exit_code == 0, result.output


def _drop_search_index(name: str = "p") -> None:
    """Simulate a DB that predates migration 0023 (no ``db_search_idx``)."""
    entry = Config.load().get_project(name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            session.execute(text("DROP TABLE db_search_idx"))
    finally:
        engine.dispose()


def test_search_finds_imported_doc(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    root = _init_project(tmp_path)
    _import_corpus(root)

    runner = CliRunner()
    result = runner.invoke(main, ["search", "-p", "p", "alpha", "--reindex"])
    assert result.exit_code == 0, result.output
    assert "alpha" in result.output


def test_search_missing_index_table_exits_clean(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    """CUR-010: no ``db_search_idx`` → clean exit 1, no traceback."""
    root = _init_project(tmp_path)
    _import_corpus(root)
    _drop_search_index()

    runner = CliRunner()
    result = runner.invoke(main, ["search", "-p", "p", "alpha"])
    assert result.exit_code == 1
    assert "db_search_idx" in result.output
    assert "Traceback" not in result.output


def test_search_reindex_flag_missing_index_table_exits_clean(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    """``--reindex`` calls ``reindex_all`` directly — same guard applies."""
    root = _init_project(tmp_path)
    _import_corpus(root)
    _drop_search_index()

    runner = CliRunner()
    result = runner.invoke(main, ["search", "-p", "p", "alpha", "--reindex"])
    assert result.exit_code == 1
    assert "db_search_idx" in result.output
    assert "Traceback" not in result.output
