"""Тесты для ``cod_doc.infra.db.db_for_entry``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

from cod_doc.config import ProjectEntry
from cod_doc.infra.db import SchemaMismatchError, db_for_entry, make_engine
from cod_doc.services.project_service import _alembic_config_for


def _migrate(db_url: str) -> None:
    from alembic import command as alembic_command

    cfg = _alembic_config_for(db_url)
    alembic_command.upgrade(cfg, "head")


def test_db_for_entry_embedded_creates_state_db(tmp_path: Path) -> None:
    entry = ProjectEntry(name="demo", path=str(tmp_path))
    factory, engine = db_for_entry(entry)

    # Файл БД появляется при первом подключении, как и раньше.
    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert journal_mode == "wal"

    state_db = tmp_path / ".cod-doc" / "state.db"
    assert state_db.exists()

    with factory() as session:
        tables = {
            row[0]
            for row in session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
    # Миграция не является ответственностью db_for_entry.
    assert "alembic_version" not in tables
    engine.dispose()


def test_db_for_entry_hub_uses_db_url(tmp_path: Path) -> None:
    hub_db = tmp_path / "hub.db"
    db_url = f"sqlite:///{hub_db}"
    _migrate(db_url)

    entry = ProjectEntry(name="hub", path=str(tmp_path), db_url=db_url)
    factory, engine = db_for_entry(entry)

    with factory() as session:
        current = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    assert current is not None
    engine.dispose()


def test_db_for_entry_hub_schema_mismatch(tmp_path: Path) -> None:
    hub_db = tmp_path / "stale_hub.db"
    engine = make_engine(f"sqlite:///{hub_db}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('000000000000')"))
    engine.dispose()

    entry = ProjectEntry(name="stale-hub", path=str(tmp_path), db_url=f"sqlite:///{hub_db}")
    with pytest.raises(SchemaMismatchError) as exc_info:
        db_for_entry(entry)

    assert exc_info.value.code == "schema_mismatch"
    assert "schema mismatch" in str(exc_info.value)


def test_db_for_entry_hub_missing_version_table(tmp_path: Path) -> None:
    hub_db = tmp_path / "empty_hub.db"
    engine = make_engine(f"sqlite:///{hub_db}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE dummy (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    entry = ProjectEntry(name="empty-hub", path=str(tmp_path), db_url=f"sqlite:///{hub_db}")
    with pytest.raises(SchemaMismatchError) as exc_info:
        db_for_entry(entry)

    assert exc_info.value.code == "schema_mismatch"
    assert "schema check failed" in str(exc_info.value)
