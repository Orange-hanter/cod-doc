"""Общие зависимости и хелперы API."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from cod_doc.config import Config
from cod_doc.core.project import Project
from cod_doc.infra.db import make_engine, make_session_factory
from cod_doc.infra.repositories import ProjectRepository

logger = logging.getLogger("cod_doc.api")

# Runtime-состояние (задаётся в lifespan)
_daemon_task: asyncio.Task[Any] | None = None
_config: Config | None = None
webhook_registry: dict[str, dict[str, Any]] = {}


def set_config(cfg: Config) -> None:
    global _config
    _config = cfg


def set_daemon_task(task: asyncio.Task[Any] | None) -> None:
    global _daemon_task
    _daemon_task = task


def get_daemon_task() -> asyncio.Task[Any] | None:
    return _daemon_task


def stop_daemon() -> bool:
    """Cancel the running daemon task. Returns True if there was a task to cancel."""
    global _daemon_task
    if _daemon_task and not _daemon_task.done():
        _daemon_task.cancel()
        _daemon_task = None
        return True
    _daemon_task = None
    return False


def start_daemon(log_callback: Callable[[str], None] | None = None) -> bool:
    """Create and launch the daemon task. Returns False if already running or not configured."""
    global _daemon_task
    from cod_doc.agent.orchestrator import run_daemon

    if _config is None or not _config.is_configured:
        return False
    if not _config.agent_enabled:
        return False
    if _daemon_task and not _daemon_task.done():
        return False
    _daemon_task = asyncio.create_task(run_daemon(_config, log_callback=log_callback))
    return True


def daemon_is_running() -> bool:
    return _daemon_task is not None and not _daemon_task.done()


def get_config() -> Config:
    if _config is None:
        raise HTTPException(500, "Конфиг не загружен")
    return _config


def get_project(name: str) -> Project:
    cfg = get_config()
    entry = cfg.get_project(name)
    if not entry:
        raise HTTPException(404, f"Проект не найден: {name}")
    return Project(entry)


# ── Per-project DB engine cache (WEB-005) ────────────────────────────────────


@dataclass
class _CachedEngine:
    engine: Engine
    mtime: float
    last_check: float


_ENGINE_CACHE: dict[Path, _CachedEngine] = {}
_ENGINE_CACHE_LOCK = threading.Lock()
_ENGINE_TTL_SECONDS = 5.0


def get_engine_for_slug(slug: str) -> Engine | None:
    """Return a cached SQLAlchemy Engine for the project's state.db, or None.

    Returns None when:
    - slug is unknown to Config,
    - .cod-doc/state.db file is absent.

    The engine is cached process-wide. Cache is invalidated when the
    state.db file's mtime changes; mtime is re-checked at most once per
    `_ENGINE_TTL_SECONDS` to avoid a stat() syscall on every request.
    """
    cfg = get_config()
    entry = cfg.get_project(slug)
    if entry is None:
        return None
    db_path = entry.cod_doc_dir / "state.db"

    with _ENGINE_CACHE_LOCK:
        cached = _ENGINE_CACHE.get(db_path)
        now = time.monotonic()

        if cached is not None:
            if now - cached.last_check < _ENGINE_TTL_SECONDS:
                return cached.engine
            try:
                current_mtime = db_path.stat().st_mtime
            except OSError:
                cached.engine.dispose()
                del _ENGINE_CACHE[db_path]
                return None
            if current_mtime == cached.mtime:
                cached.last_check = now
                return cached.engine
            # mtime changed → invalidate, fall through to recreate
            cached.engine.dispose()
            del _ENGINE_CACHE[db_path]

        try:
            mtime = db_path.stat().st_mtime
        except OSError:
            return None

        engine = make_engine(f"sqlite:///{db_path}")
        _ENGINE_CACHE[db_path] = _CachedEngine(engine=engine, mtime=mtime, last_check=now)
        return engine


def dispose_all_engines() -> None:
    """Dispose all cached engines and clear the cache. Called on app shutdown."""
    with _ENGINE_CACHE_LOCK:
        for cached in _ENGINE_CACHE.values():
            cached.engine.dispose()
        _ENGINE_CACHE.clear()


def get_project_db(slug: str) -> Iterator[tuple[Session, int]]:
    """FastAPI dependency: yield (Session, project_db_id) or raise HTTPException(404).

    Use `Depends(get_project_db)` in endpoints that REQUIRE a DB-initialized project.
    For pages that gracefully render without DB, use `try_open_project_db()`
    as a context manager instead.
    """
    engine = get_engine_for_slug(slug)
    if engine is None:
        raise HTTPException(404, f"DB-проект не инициализирован: {slug}")
    factory = make_session_factory(engine)
    session = factory()
    try:
        try:
            proj = ProjectRepository(session).get_by_slug(slug)
        except OperationalError as exc:
            raise HTTPException(404, f"Схема БД не накатана: {slug}") from exc
        if proj is None or proj.row_id is None:
            raise HTTPException(404, f"Проект не найден в БД: {slug}")
        yield (session, proj.row_id)
    finally:
        session.close()


@contextmanager
def try_open_project_db(slug: str) -> Iterator[tuple[Session | None, int | None]]:
    """Graceful context manager: yield (None, None) if DB unavailable.

    Used by list pages that show a 'DB not initialized' warning instead of 404.
    """
    engine = get_engine_for_slug(slug)
    if engine is None:
        yield (None, None)
        return
    factory = make_session_factory(engine)
    session = factory()
    try:
        try:
            proj = ProjectRepository(session).get_by_slug(slug)
        except OperationalError:
            yield (None, None)
            return
        if proj is None or proj.row_id is None:
            yield (None, None)
        else:
            yield (session, proj.row_id)
    finally:
        session.close()
