"""STO-021: `get_engine_for_slug` уважает `db_url` записи реестра.

До правки web и `/api/*` резолвили БД как `<root>/.cod-doc/state.db` и
игнорировали `entry.db_url`, из-за чего hub-проект (БД вынесена за пределы
репозитория) был виден CLI и MCP, но не UI.

Резолв-истина — `cod_doc.infra.db.db_for_entry`; здесь проверяется, что
веб-слой ходит ровно туда же, сохраняя mtime-кэш для файловой sqlite.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from cod_doc.api import deps
from cod_doc.api.deps import get_engine_for_slug, get_project_db, set_config
from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from tests._alembic import run_alembic


def _make_db(db_path: Path, slug: str, root: Path) -> None:
    """Накатить голову миграций и завести строку проекта в готовой БД."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    engine = make_engine(f"sqlite:///{db_path}")
    with transactional(make_session_factory(engine)) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug=slug, title=slug.title(), root_path=str(root), config={})
        )
        proj.created = now
        proj.updated = now
    engine.dispose()


def _register(entry: ProjectEntry) -> None:
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)
    set_config(cfg)


@pytest.fixture
def embedded_project(tmp_path: Path):
    """Проект без `db_url` — БД лежит в `<root>/.cod-doc/state.db`."""
    root = tmp_path / "embedded"
    db_path = root / ".cod-doc" / "state.db"
    _make_db(db_path, "embedded", root)

    entry = ProjectEntry(name="embedded", path=str(root))
    _register(entry)
    return entry, db_path


@pytest.fixture
def hub_project(tmp_path: Path):
    """Проект с `db_url` — БД вынесена за пределы каталога проекта."""
    root = tmp_path / "hubbed"
    (root / ".cod-doc").mkdir(parents=True)
    hub_db = tmp_path / "hub" / "cod-doc.db"
    _make_db(hub_db, "hubbed", root)

    entry = ProjectEntry(name="hubbed", path=str(root), db_url=f"sqlite:///{hub_db}")
    _register(entry)
    return entry, hub_db


# ── db_url пустой: поведение ровно как раньше ────────────────────────────────


def test_embedded_project_opens_repo_state_db(embedded_project) -> None:
    entry, db_path = embedded_project
    engine = get_engine_for_slug(entry.name)
    assert engine is not None
    assert engine.url.database == str(db_path)
    assert list(deps._ENGINE_CACHE) == [str(db_path)]


def test_embedded_project_engine_is_cached(embedded_project) -> None:
    entry, _ = embedded_project
    assert get_engine_for_slug(entry.name) is get_engine_for_slug(entry.name)


# ── db_url задан: hub-БД, а не embedded ──────────────────────────────────────


def test_hub_project_opens_db_url(hub_project) -> None:
    """Движок смотрит в файл из `db_url`, а не в `<root>/.cod-doc/state.db`."""
    entry, hub_db = hub_project
    engine = get_engine_for_slug(entry.name)
    assert engine is not None
    assert engine.url.database == str(hub_db)
    # Embedded-путь не трогаем и не создаём — иначе получили бы пустую БД.
    assert not (entry.cod_doc_dir / "state.db").exists()


def test_hub_project_visible_through_get_project_db(hub_project) -> None:
    """`Depends(get_project_db)` находит проект в hub-БД, а не отдаёт 404."""
    entry, _ = hub_project
    gen = get_project_db(entry.name)
    session, project_id = next(gen)
    try:
        assert isinstance(project_id, int)
        assert ProjectRepository(session).get_by_slug(entry.name) is not None
    finally:
        gen.close()


def test_hub_project_engine_is_cached(hub_project) -> None:
    entry, _ = hub_project
    assert get_engine_for_slug(entry.name) is get_engine_for_slug(entry.name)


def test_hub_sqlite_keeps_mtime_invalidation(hub_project, monkeypatch) -> None:
    """Для файловой hub-БД mtime-кэш работает так же, как для embedded."""
    entry, hub_db = hub_project
    first = get_engine_for_slug(entry.name)

    monkeypatch.setattr(deps, "_ENGINE_TTL_SECONDS", 0.0)
    new_mtime = hub_db.stat().st_mtime + 5
    os.utime(hub_db, (new_mtime, new_mtime))

    second = get_engine_for_slug(entry.name)
    assert second is not None
    assert second is not first


def test_hub_db_absent_returns_none(tmp_path: Path) -> None:
    """`db_url` указывает на несуществующий файл → None, файл не создаётся."""
    root = tmp_path / "ghost"
    root.mkdir()
    missing = tmp_path / "nowhere" / "state.db"
    _register(ProjectEntry(name="ghost", path=str(root), db_url=f"sqlite:///{missing}"))

    assert get_engine_for_slug("ghost") is None
    assert not missing.exists()
    assert deps._ENGINE_CACHE == {}


def test_hub_schema_mismatch_returns_none(tmp_path: Path, caplog) -> None:
    """БД не на голове миграций → None (404/«DB not initialized»), не исключение."""
    root = tmp_path / "stale"
    root.mkdir()
    stale_db = tmp_path / "stale-hub" / "state.db"
    stale_db.parent.mkdir(parents=True)
    stale_db.touch()  # пустой файл — валидная sqlite без alembic_version
    _register(ProjectEntry(name="stale", path=str(root), db_url=f"sqlite:///{stale_db}"))

    with caplog.at_level("WARNING", logger="cod_doc.api"):
        assert get_engine_for_slug("stale") is None
    assert deps._ENGINE_CACHE == {}
    assert any("db_url" in rec.message for rec in caplog.records)


# ── db_url не-sqlite: кэш по URL, без mtime ──────────────────────────────────


def test_non_sqlite_db_url_cached_by_url(tmp_path: Path, monkeypatch) -> None:
    """Postgres-URL кэшируется по URL и не инвалидируется по mtime."""
    root = tmp_path / "pg"
    root.mkdir()
    db_url = "postgresql+psycopg://user@localhost:5432/cod_doc"
    _register(ProjectEntry(name="pg", path=str(root), db_url=db_url))

    fake = make_engine("sqlite://")  # in-memory: файла на диске нет
    calls = 0

    def fake_db_for_entry(entry):
        nonlocal calls
        calls += 1
        return make_session_factory(fake), fake

    monkeypatch.setattr(deps, "db_for_entry", fake_db_for_entry)
    # TTL=0 заставляет проверку кэша идти по полному пути, а не по короткому.
    monkeypatch.setattr(deps, "_ENGINE_TTL_SECONDS", 0.0)

    first = get_engine_for_slug("pg")
    second = get_engine_for_slug("pg")

    assert first is fake
    assert second is fake
    assert calls == 1, "движок сетевой БД не должен пересоздаваться"
    assert list(deps._ENGINE_CACHE) == [db_url]
    assert deps._ENGINE_CACHE[db_url].mtime is None


def test_unusable_db_url_does_not_raise(tmp_path: Path) -> None:
    """Мусор в `db_url` (нет драйвера / не URL) — None, а не 500."""
    root = tmp_path / "broken"
    root.mkdir()
    _register(ProjectEntry(name="broken", path=str(root), db_url="не-урл-вовсе"))

    assert get_engine_for_slug("broken") is None
    assert deps._ENGINE_CACHE == {}


# ── резолв пути под sqlite-URL ───────────────────────────────────────────────
# STO-027: сам резолв (`sqlite_file_path`, `db_url_for_entry`) переехал в
# `infra.db` — его таблица случаев живёт в tests/infra/test_db_for_entry.py.
# Здесь остаётся проверка, что веб-слой берёт из него ключ кэша и файл.


def test_cache_key_follows_db_url(tmp_path: Path) -> None:
    """Ключ кэша движка — файл hub-БД, а не embedded-путь проекта."""
    root = tmp_path / "repo"
    hub_db = tmp_path / "hub" / "state.db"
    _make_db(hub_db, "keyed", root)

    entry = ProjectEntry(name="keyed", path=str(root), db_url=f"sqlite:///{hub_db}")
    _register(entry)

    assert get_engine_for_slug("keyed") is not None
    assert set(deps._ENGINE_CACHE) == {str(hub_db)}
