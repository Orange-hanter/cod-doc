"""Database engine and session factory."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine.interfaces import DBAPIConnection
    from sqlalchemy.pool import ConnectionPoolEntry

    from cod_doc.config import ProjectEntry

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


def sqlite_file_path(url: str) -> Path | None:
    """Путь к файлу для файловой sqlite-URL; ``None`` для памяти и не-sqlite.

    Нужен всем, кто хочет знать «а есть ли эта БД на диске»: web кэширует
    движок по mtime файла, ``init_project`` проверяет, создаёт он БД или
    мигрирует существующую. Для postgres и ``:memory:`` ответа нет — ``None``.
    """
    try:
        parsed = make_url(url)
    except ArgumentError:
        return None
    if not parsed.get_backend_name().startswith("sqlite"):
        return None
    if not parsed.database or is_in_memory_sqlite(url):
        return None
    return Path(parsed.database)


def db_url_for_entry(entry: ProjectEntry) -> str:
    """URL БД записи реестра: непустой ``db_url`` (hub) выигрывает у embedded.

    STO-021/STO-027: единственная точка вывода «какую БД открывает проект».
    ``db_for_entry`` открывает именно её, ``init_project`` именно её мигрирует,
    ``api/deps`` на неё же вешает кэш движков — раньше правило было размазано
    по трём резолверам и расходилось.

    Функция чистая: каталог под embedded-файл создаёт уже ``db_for_entry``,
    иначе простой резолв URL создавал бы `.cod-doc/` на каждый запрос к web.
    """
    db_url = getattr(entry, "db_url", None)
    if db_url:
        return str(db_url)
    root_path = Path(getattr(entry, "path", ".")).expanduser().resolve()
    return f"sqlite:///{root_path / DEFAULT_EMBEDDED_PATH}"


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine with sensible defaults."""
    final_url = url or resolve_db_url()
    engine = create_engine(final_url, echo=echo, future=True)
    register_sqlite_pragmas(engine, final_url)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class SchemaMismatchError(Exception):
    """БД накатана не до актуальной alembic-головы."""

    code = "schema_mismatch"


def _alembic_head_revision() -> str:
    """Текущая голова поставляемых миграций."""
    from importlib.resources import files

    from alembic.script import ScriptDirectory

    scripts_path = files("cod_doc.infra.migrations")
    script = ScriptDirectory(str(scripts_path))
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("no alembic migrations found")
    return head


def db_for_entry(entry: ProjectEntry) -> tuple[sessionmaker[Session], Engine]:
    """Фабрика сессий и движок для записи проекта.

    - Если у записи задан ``db_url`` — открываем эту БД (hub-режим) и перед
      возвратом проверяем, что ``alembic_version`` совпадает с головой.
    - Иначе — embedded ``<root>/.cod-doc/state.db``.
    """
    hub = bool(getattr(entry, "db_url", None))
    url = db_url_for_entry(entry)
    if not hub:
        db_path = sqlite_file_path(url)
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = make_engine(url)

    if hub:
        head = _alembic_head_revision()
        try:
            with engine.connect() as conn:
                current = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
        except Exception as exc:
            engine.dispose()
            raise SchemaMismatchError(
                f"hub schema check failed for {getattr(entry, 'name', '<unknown>')!r}: {exc}"
            ) from exc
        if current != head:
            engine.dispose()
            raise SchemaMismatchError(
                f"hub schema mismatch for {getattr(entry, 'name', '<unknown>')!r}: "
                f"expected {head!r}, found {current!r}"
            )

    return make_session_factory(engine), engine


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
