"""Тесты для резолва БД записи реестра: ``db_url_for_entry`` и ``db_for_entry``."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from sqlalchemy import text

from cod_doc.config import ProjectEntry
from cod_doc.infra.db import (
    SchemaMismatchError,
    db_for_entry,
    db_url_for_entry,
    make_engine,
    sqlite_file_path,
)
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


# ── STO-027: единственная точка вывода «какую БД открывает проект» ───────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite:////abs/path/state.db", PurePosixPath("/abs/path/state.db")),
        ("sqlite:///relative.db", PurePosixPath("relative.db")),
        ("sqlite+pysqlite:////abs/x.db", PurePosixPath("/abs/x.db")),
        ("sqlite:///:memory:", None),
        ("sqlite://", None),
        ("postgresql+psycopg://user@localhost/db", None),
        ("не-урл-вовсе", None),
    ],
)
def test_sqlite_file_path(url: str, expected: PurePosixPath | None) -> None:
    resolved = sqlite_file_path(url)
    assert resolved == (None if expected is None else Path(expected))


def test_db_url_for_entry_embedded(tmp_path: Path) -> None:
    entry = ProjectEntry(name="demo", path=str(tmp_path))
    assert db_url_for_entry(entry) == f"sqlite:///{tmp_path.resolve() / '.cod-doc' / 'state.db'}"


def test_db_url_for_entry_hub_wins(tmp_path: Path) -> None:
    entry = ProjectEntry(name="demo", path=str(tmp_path), db_url="sqlite:////hub/state.db")
    assert db_url_for_entry(entry) == "sqlite:////hub/state.db"


def test_db_url_for_entry_empty_db_url_is_embedded(tmp_path: Path) -> None:
    """Пустая строка в реестре — это «не задано», а не «открывай ''»."""
    entry = ProjectEntry(name="demo", path=str(tmp_path), db_url="")
    assert db_url_for_entry(entry) == f"sqlite:///{tmp_path.resolve() / '.cod-doc' / 'state.db'}"


def test_db_url_for_entry_does_not_touch_disk(tmp_path: Path) -> None:
    """Резолв URL чистый: каталог `.cod-doc/` создаёт уже `db_for_entry`."""
    repo = tmp_path / "untouched"
    repo.mkdir()
    db_url_for_entry(ProjectEntry(name="demo", path=str(repo)))
    assert not (repo / ".cod-doc").exists()
