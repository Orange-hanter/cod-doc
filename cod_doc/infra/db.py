"""Database engine and session factory."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine.interfaces import DBAPIConnection
    from sqlalchemy.pool import ConnectionPoolEntry

DEFAULT_EMBEDDED_PATH = ".cod-doc/state.db"

#: Ждать освобождения блокировки перед `database is locked` (мс).
#: SYM-002 / RFC 22 §3.1 — несколько петель агентов пишут в одну БД.
SQLITE_BUSY_TIMEOUT_MS = 5000

#: Прагмы для файловой SQLite. WAL даёт «писатель не блокирует читателей»,
#: `synchronous=NORMAL` безопасен именно в паре с WAL (fsync только на
#: checkpoint'ах). `journal_mode` возвращает строку — ответ обязательно
#: вычитывается, иначе pysqlite оставит незакрытый курсор.
SQLITE_FILE_PRAGMAS: tuple[str, ...] = (
    "PRAGMA foreign_keys=ON",
    "PRAGMA journal_mode=WAL",
    f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}",
    "PRAGMA synchronous=NORMAL",
)

#: Для in-memory БД WAL и synchronous смысла не имеют — журнала нет вовсе.
SQLITE_MEMORY_PRAGMAS: tuple[str, ...] = ("PRAGMA foreign_keys=ON",)

_IN_MEMORY_MARKERS = (":memory:", "mode=memory")


def is_in_memory_sqlite(url: str) -> bool:
    """`sqlite://` без пути, `:memory:` и `mode=memory` — это БД в памяти."""
    if any(marker in url for marker in _IN_MEMORY_MARKERS):
        return True
    return url.rstrip("/") in {"sqlite:", "sqlite:/", "sqlite://"}


def apply_sqlite_pragmas(dbapi_conn: DBAPIConnection, *, in_memory: bool) -> None:
    """Выполнить набор прагм на свежесозданном соединении."""
    pragmas = SQLITE_MEMORY_PRAGMAS if in_memory else SQLITE_FILE_PRAGMAS
    cur = dbapi_conn.cursor()
    try:
        for pragma in pragmas:
            cur.execute(pragma)
            cur.fetchall()
    finally:
        cur.close()


def register_sqlite_pragmas(engine: Engine, url: str) -> None:
    """Повесить connect-listener с прагмами, если движок — SQLite.

    Общая точка для `make_engine` и alembic-окружения: alembic строит engine
    через `engine_from_config` и своего listener'а не получает, поэтому шёл бы
    без `busy_timeout`.
    """
    if not url.startswith("sqlite"):
        return
    in_memory = is_in_memory_sqlite(url)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        apply_sqlite_pragmas(dbapi_conn, in_memory=in_memory)


def resolve_db_url(project_root: Path | None = None, override: str | None = None) -> str:
    """Resolve DB URL from override → env → embedded default.

    embedded mode: sqlite at <project_root>/.cod-doc/state.db
    server mode:   COD_DOC_DB_URL env var (postgres://...)
    """
    if override:
        return override
    env = os.environ.get("COD_DOC_DB_URL")
    if env:
        return env
    if project_root is None:
        project_root = Path.cwd()
    path = project_root / DEFAULT_EMBEDDED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine with sensible defaults."""
    final_url = url or resolve_db_url()
    engine = create_engine(final_url, echo=echo, future=True)
    register_sqlite_pragmas(engine, final_url)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def transactional(
    session_factory: sessionmaker[Session],
    *,
    commit: bool = True,
) -> Iterator[Session]:
    """Context manager: open session, commit on success, rollback on error.

    ``commit=False`` (PCA-944) is the ``dry_run`` shape: the block runs and
    validation/errors still bubble up, but the session is rolled back at
    the end instead of committed. Useful for ``dry_run=True`` MCP-tool
    paths that want to validate plus return the would-be result without
    persisting any rows.
    """
    session = session_factory()
    try:
        yield session
        if commit:
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
