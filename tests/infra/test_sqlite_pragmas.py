"""SYM-002: WAL + busy_timeout + synchronous=NORMAL и dialect-guard FTS5.

RFC 22 §3.1, находки B7/B10. Проверяем ровно acceptance-критерии задачи:
`journal_mode` → wal, два конкурентных писателя без `database is locked`,
FTS5-миграция на не-SQLite падает явной ошибкой.
"""

from __future__ import annotations

import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from cod_doc.infra.db import (
    SQLITE_BUSY_TIMEOUT_MS,
    is_in_memory_sqlite,
    make_engine,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

REPO_ROOT = Path(__file__).resolve().parents[2]
FTS5_MIGRATION = (
    REPO_ROOT / "cod_doc" / "infra" / "migrations" / "versions" / "20260515_0023_fts5_index.py"
)

SYNCHRONOUS_NORMAL = 1
WRITERS = 2
ROWS_PER_WRITER = 40


@pytest.fixture
def file_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = make_engine(f"sqlite:///{tmp_path / 'pragmas.db'}")
    yield engine
    engine.dispose()


def _pragma(engine: Engine, name: str) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(f"PRAGMA {name}")).scalar()


def test_file_db_uses_wal(file_engine: Engine) -> None:
    assert _pragma(file_engine, "journal_mode") == "wal"
    assert _pragma(file_engine, "busy_timeout") == SQLITE_BUSY_TIMEOUT_MS
    assert _pragma(file_engine, "synchronous") == SYNCHRONOUS_NORMAL
    assert _pragma(file_engine, "foreign_keys") == 1


def test_memory_db_skips_wal() -> None:
    engine = make_engine("sqlite://")
    try:
        # Журнала у in-memory БД нет — WAL к ней неприменим, но FK нужны.
        assert _pragma(engine, "journal_mode") == "memory"
        assert _pragma(engine, "foreign_keys") == 1
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite://", True),
        ("sqlite:///:memory:", True),
        ("sqlite:///file:x?mode=memory&cache=shared", True),
        ("sqlite:////tmp/state.db", False),
        ("sqlite:///.cod-doc/state.db", False),
    ],
)
def test_in_memory_detection(url: str, *, expected: bool) -> None:
    assert is_in_memory_sqlite(url) is expected


def test_concurrent_writers_no_lock(file_engine: Engine) -> None:
    """Acceptance SYM-002: два писателя в одну БД без `database is locked`."""
    with file_engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (row_id INTEGER PRIMARY KEY, who TEXT NOT NULL)"))

    def write(who: str) -> None:
        for _ in range(ROWS_PER_WRITER):
            with file_engine.begin() as conn:
                conn.execute(text("INSERT INTO t (who) VALUES (:who)"), {"who": who})

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        futures = [pool.submit(write, f"writer-{i}") for i in range(WRITERS)]
        for future in futures:
            try:
                future.result()
            except OperationalError as exc:  # pragma: no cover - падение = регрессия
                pytest.fail(f"конкурентная запись упала: {exc}")

    with file_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM t")).scalar() == WRITERS * ROWS_PER_WRITER


def _load_fts5_migration() -> Any:
    """Импорт по пути: имя файла начинается с цифр, обычный import невозможен."""
    spec = importlib.util.spec_from_file_location("_fts5_migration", FTS5_MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_fts5_migration_rejects_non_sqlite(monkeypatch: pytest.MonkeyPatch, direction: str) -> None:
    """Acceptance SYM-002: `alembic upgrade head` на Postgres падает явно.

    Прогон настоящего alembic против Postgres-URL потребовал бы установленного
    `psycopg`, которого нет в dev-зависимостях, поэтому подменяем bind.
    """
    module = _load_fts5_migration()
    fake_bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    monkeypatch.setattr(module.op, "get_bind", lambda: fake_bind)

    with pytest.raises(NotImplementedError, match="postgresql"):
        getattr(module, direction)()


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_fts5_migration_allows_sqlite(monkeypatch: pytest.MonkeyPatch, direction: str) -> None:
    module = _load_fts5_migration()
    executed: list[str] = []
    fake_bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    monkeypatch.setattr(module.op, "get_bind", lambda: fake_bind)
    monkeypatch.setattr(module.op, "execute", executed.append)

    getattr(module, direction)()
    assert executed, "миграция на sqlite обязана выполнить DDL"
