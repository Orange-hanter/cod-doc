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
    if final_url.startswith("sqlite"):
        # Foreign keys are off by default in SQLite — turn them on per connection.
        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

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
