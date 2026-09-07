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

from fastapi import HTTPException, Request
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.infra.db import (
    SchemaMismatchError,
    db_for_entry,
    is_in_memory_sqlite,
    make_engine,
    make_session_factory,
)
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


# SYM-003 / ADO-035: эндпоинты, пишущие LLM-ключ в config.yaml (POST
# /settings, PATCH /api/config), принимаем только с loopback. request.client
# приходит из сокета (не из заголовков), поэтому подделать его удалённо
# нельзя; X-Forwarded-For сознательно не доверяем — прокси-развёртывания
# должны терминировать на loopback.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# Сентинел Starlette TestClient: реальный TCP-пир никогда не представит эту
# строку, поэтому допускать её безопасно (нужно для in-process web-тестов).
_TESTCLIENT_HOST = "testclient"


def ensure_loopback_client(request: Request) -> None:
    """403, если запрос пришёл не с loopback-интерфейса."""
    host = request.client.host if request.client is not None else None
    if host in _LOOPBACK_HOSTS or host == _TESTCLIENT_HOST:
        return
    raise HTTPException(status_code=403, detail="This endpoint is loopback-only")


def get_project(name: str) -> Project:
    cfg = get_config()
    entry = cfg.get_project(name)
    if not entry:
        raise HTTPException(404, f"Проект не найден: {name}")
    return Project(entry)


# ── Per-project DB engine cache (WEB-005, STO-021) ───────────────────────────


@dataclass
class _CachedEngine:
    engine: Engine
    #: mtime файла sqlite на момент создания движка. ``None`` — БД не файловая
    #: (hub на postgres и т.п.), инвалидировать по mtime нечего.
    mtime: float | None
    last_check: float


@dataclass(frozen=True)
class _DbTarget:
    """Куда смотрит запись проекта: ключ кэша, файл sqlite и режим.

    ``path`` заполнен только для файловой sqlite — по нему работает
    mtime-инвалидация. Для сетевой БД ключ кэша — сам URL.
    """

    cache_key: str
    path: Path | None
    hub: bool


_ENGINE_CACHE: dict[str, _CachedEngine] = {}
_ENGINE_CACHE_LOCK = threading.Lock()
_ENGINE_TTL_SECONDS = 5.0


def _sqlite_file_path(url: str) -> Path | None:
    """Путь к файлу для файловой sqlite-URL; ``None`` для памяти и не-sqlite."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        return None
    if not parsed.get_backend_name().startswith("sqlite"):
        return None
    if not parsed.database or is_in_memory_sqlite(url):
        return None
    return Path(parsed.database)


def _resolve_db_target(entry: ProjectEntry) -> _DbTarget:
    """Резолв БД записи проекта — тот же, что в ``infra.db.db_for_entry``.

    Непустой ``db_url`` (hub-режим) выигрывает у embedded
    ``<root>/.cod-doc/state.db``; пустой — оставляет embedded-путь.
    """
    db_url = entry.db_url or ""
    if not db_url:
        embedded = entry.cod_doc_dir / "state.db"
        return _DbTarget(cache_key=str(embedded), path=embedded, hub=False)
    db_path = _sqlite_file_path(db_url)
    cache_key = str(db_path) if db_path is not None else db_url
    return _DbTarget(cache_key=cache_key, path=db_path, hub=True)


def _drop_cached(cache_key: str, cached: _CachedEngine) -> None:
    """Выбросить запись из кэша вместе с движком. Под ``_ENGINE_CACHE_LOCK``."""
    cached.engine.dispose()
    _ENGINE_CACHE.pop(cache_key, None)


def _reuse_cached(target: _DbTarget, cached: _CachedEngine, now: float) -> Engine | None:
    """Живой движок из кэша либо ``None``, если запись протухла.

    Протухшая запись тут же выбрасывается из кэша. Под ``_ENGINE_CACHE_LOCK``.
    """
    if now - cached.last_check < _ENGINE_TTL_SECONDS:
        return cached.engine
    if target.path is None:
        # Сетевая БД: stat'ить нечего, только продлеваем TTL.
        cached.last_check = now
        return cached.engine
    try:
        current_mtime = target.path.stat().st_mtime
    except OSError:
        _drop_cached(target.cache_key, cached)
        return None
    if current_mtime != cached.mtime:
        _drop_cached(target.cache_key, cached)
        return None
    cached.last_check = now
    return cached.engine


def _create_engine_for_target(
    entry: ProjectEntry, target: _DbTarget
) -> tuple[Engine, float | None] | None:
    """Создать движок под ``target``; ``None`` — БД недоступна.

    Hub-режим делегируется ``db_for_entry``, чтобы резолв и сверка
    alembic-головы жили в одном месте (``infra.db``).
    """
    mtime: float | None = None
    if target.path is not None:
        try:
            mtime = target.path.stat().st_mtime
        except OSError:
            return None
    if not target.hub:
        return make_engine(f"sqlite:///{target.path}"), mtime
    try:
        _factory, engine = db_for_entry(entry)
    except (SchemaMismatchError, SQLAlchemyError) as exc:
        logger.warning("Проект %s: БД из db_url недоступна — %s", entry.name, exc)
        return None
    return engine, mtime


def get_engine_for_slug(slug: str) -> Engine | None:
    """Return a cached SQLAlchemy Engine for the project's DB, or None.

    Резолв БД совпадает с ``infra.db.db_for_entry`` (STO-021): непустой
    ``db_url`` записи реестра открывает hub-БД (со сверкой alembic-головы),
    пустой — embedded ``<root>/.cod-doc/state.db``.

    Returns None when:
    - slug is unknown to Config,
    - the sqlite file behind the entry is absent,
    - hub-БД недоступна или её схема разъехалась с головой миграций
      (``SchemaMismatchError``) — веб-слой отдаёт «DB not initialized» / 404,
      а не 500.

    The engine is cached process-wide. Для файловой sqlite кэш инвалидируется
    по mtime файла, который проверяется не чаще раза в ``_ENGINE_TTL_SECONDS``
    (иначе stat() был бы на каждом запросе). Для нефайловой БД mtime
    неприменим: движок кэшируется по URL и живёт до ``dispose_all_engines()``.
    """
    cfg = get_config()
    entry = cfg.get_project(slug)
    if entry is None:
        return None
    target = _resolve_db_target(entry)

    with _ENGINE_CACHE_LOCK:
        now = time.monotonic()
        cached = _ENGINE_CACHE.get(target.cache_key)
        if cached is not None:
            reused = _reuse_cached(target, cached, now)
            if reused is not None:
                return reused

        created = _create_engine_for_target(entry, target)
        if created is None:
            return None
        engine, mtime = created
        _ENGINE_CACHE[target.cache_key] = _CachedEngine(engine=engine, mtime=mtime, last_check=now)
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
