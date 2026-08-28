"""Тесты для ``cod-doc hub init``."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from cod_doc.cli import main


def test_hub_init_creates_migrated_hub_db(isolated_cod_doc_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["hub", "init"])

    assert result.exit_code == 0, result.output
    hub_db = isolated_cod_doc_home / "hub.db"
    assert hub_db.exists()

    with sqlite3.connect(str(hub_db)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert journal_mode == "wal"
    assert version is not None


def test_hub_init_is_idempotent(isolated_cod_doc_home: Path) -> None:
    runner = CliRunner()
    result1 = runner.invoke(main, ["hub", "init"])
    assert result1.exit_code == 0, result1.output

    runner = CliRunner()
    result2 = runner.invoke(main, ["hub", "init"])
    assert result2.exit_code == 0, result2.output
    assert "готова" in result2.output
