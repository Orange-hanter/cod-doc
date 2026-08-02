"""WEB-005: per-project DB engine cache + DI helpers in cod_doc.api.deps."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from cod_doc.api import deps

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
from cod_doc.api.deps import (
    dispose_all_engines,
    get_engine_for_slug,
    get_project_db,
    set_config,
    try_open_project_db,
)
from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_upgrade(db_url: str) -> None:
    from tests._alembic import run_alembic_upgrade

    run_alembic_upgrade(db_url)


@pytest.fixture
def configured_project(tmp_path: Path):
    repo = tmp_path / "demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    _alembic_upgrade(f"sqlite:///{db_path}")

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)
    set_config(cfg)

    # Seed ProjectModel with matching slug.
    from cod_doc.infra.db import make_engine

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
    engine.dispose()

    return entry, db_path


def test_engine_cache_returns_same_engine(configured_project) -> None:
    """Two consecutive lookups for the same slug return the same Engine instance."""
    entry, _ = configured_project
    e1 = get_engine_for_slug(entry.name)
    e2 = get_engine_for_slug(entry.name)
    assert e1 is not None
    assert e1 is e2  # identity, not equality


def test_engine_cache_returns_none_for_unknown_slug(configured_project) -> None:
    assert get_engine_for_slug("nope-does-not-exist") is None


def test_engine_cache_returns_none_when_db_absent(tmp_path: Path) -> None:
    """No state.db file → None, no engine created."""
    repo = tmp_path / "no-db"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)
    set_config(cfg)

    assert get_engine_for_slug("bare") is None
    # Cache stays empty.
    assert deps._ENGINE_CACHE == {}


def test_engine_cache_invalidates_on_mtime_change(configured_project, monkeypatch) -> None:
    """When state.db mtime changes (DB rebuilt), the cache is invalidated."""
    entry, db_path = configured_project
    e1 = get_engine_for_slug(entry.name)

    # Force the next lookup to perform an mtime check (skip TTL).
    monkeypatch.setattr(deps, "_ENGINE_TTL_SECONDS", 0.0)

    # Bump mtime — touch the file forward by 5 seconds.
    new_mtime = db_path.stat().st_mtime + 5
    os.utime(db_path, (new_mtime, new_mtime))

    e2 = get_engine_for_slug(entry.name)
    assert e2 is not None
    assert e2 is not e1, "cache should have been invalidated and engine recreated"


def test_engine_cache_invalidates_when_db_deleted(configured_project, monkeypatch) -> None:
    """If state.db disappears between requests, cache returns None and clears entry."""
    entry, db_path = configured_project
    get_engine_for_slug(entry.name)
    assert len(deps._ENGINE_CACHE) == 1

    monkeypatch.setattr(deps, "_ENGINE_TTL_SECONDS", 0.0)
    db_path.unlink()

    assert get_engine_for_slug(entry.name) is None
    assert deps._ENGINE_CACHE == {}


def test_engine_cache_ttl_skips_stat(configured_project, monkeypatch) -> None:
    """Within TTL window, mtime is NOT re-checked (count stat() calls)."""
    entry, db_path = configured_project
    get_engine_for_slug(entry.name)

    stat_count = 0
    real_stat = Path.stat

    def counting_stat(self: Path, *args, **kwargs):
        nonlocal stat_count
        if self == db_path:
            stat_count += 1
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counting_stat)

    # TTL is 5s by default; this lookup must NOT stat the db file.
    get_engine_for_slug(entry.name)
    assert stat_count == 0


def test_dispose_all_engines_clears_cache(configured_project) -> None:
    entry, _ = configured_project
    get_engine_for_slug(entry.name)
    assert len(deps._ENGINE_CACHE) == 1
    dispose_all_engines()
    assert deps._ENGINE_CACHE == {}


# ── try_open_project_db (graceful) ───────────────────────────────────────────


def test_try_open_yields_session_when_ready(configured_project) -> None:
    entry, _ = configured_project
    with try_open_project_db(entry.name) as (session, project_id):
        assert session is not None
        assert isinstance(project_id, int)


def test_try_open_yields_none_when_db_missing(tmp_path: Path) -> None:
    repo = tmp_path / "no-db"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)
    set_config(cfg)

    with try_open_project_db("bare") as (session, project_id):
        assert session is None
        assert project_id is None


def test_try_open_yields_none_for_unknown_slug(tmp_path: Path) -> None:
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    set_config(cfg)
    with try_open_project_db("nope") as (session, project_id):
        assert session is None
        assert project_id is None


# ── get_project_db (strict, FastAPI Depends) ─────────────────────────────────


def test_get_project_db_strict_yields(configured_project) -> None:
    entry, _ = configured_project
    gen = get_project_db(entry.name)
    session, project_id = next(gen)
    try:
        assert session is not None
        assert isinstance(project_id, int)
    finally:
        gen.close()


def test_get_project_db_strict_raises_404_when_db_missing(tmp_path: Path) -> None:
    repo = tmp_path / "no-db"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)
    set_config(cfg)

    with pytest.raises(HTTPException) as exc:
        next(get_project_db("bare"))
    assert exc.value.status_code == 404


def test_get_project_db_strict_raises_404_for_unknown_slug(tmp_path: Path) -> None:
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    set_config(cfg)
    with pytest.raises(HTTPException) as exc:
        next(get_project_db("nope"))
    assert exc.value.status_code == 404


def test_get_project_db_works_as_fastapi_dependency(configured_project) -> None:
    """Smoke: register a FastAPI route using Depends(get_project_db) and call it."""
    from fastapi import Depends

    entry, _ = configured_project
    app = FastAPI()

    @app.get("/probe/{slug}")
    def probe(slug: str, db: tuple[Session, int] = Depends(get_project_db)) -> dict[str, int]:
        session, project_id = db
        # Real query to verify session is alive.
        assert session.is_active
        return {"project_id": project_id}

    with TestClient(app) as client:
        r = client.get(f"/probe/{entry.name}")
        assert r.status_code == 200
        assert isinstance(r.json()["project_id"], int)

        r404 = client.get("/probe/nope")
        assert r404.status_code == 404


# ── Perf benchmark (counter-based, not wall-clock) ───────────────────────────


def test_engine_cache_avoids_recreation_on_repeated_lookups(
    configured_project, monkeypatch
) -> None:
    """100 lookups create the engine exactly once — proves the cache works."""
    entry, _ = configured_project
    create_count = 0
    original_make_engine = deps.make_engine

    def counting_make_engine(*args, **kwargs):
        nonlocal create_count
        create_count += 1
        return original_make_engine(*args, **kwargs)

    monkeypatch.setattr(deps, "make_engine", counting_make_engine)

    for _ in range(100):
        engine = get_engine_for_slug(entry.name)
        assert engine is not None

    assert create_count == 1


def test_engine_cache_perf_smoke(configured_project) -> None:
    """Sanity check: cached path is meaningfully faster than 'cold' creation.

    Not a strict assertion — CI noise can flip wall-clock comparisons.
    Logs the numbers so we can eyeball them in CI artifacts.
    """
    entry, _ = configured_project

    # Cold: dispose then time first lookup.
    dispose_all_engines()
    t0 = time.perf_counter()
    get_engine_for_slug(entry.name)
    cold_us = (time.perf_counter() - t0) * 1_000_000

    # Warm: 100 cached lookups.
    t1 = time.perf_counter()
    for _ in range(100):
        get_engine_for_slug(entry.name)
    warm_avg_us = (time.perf_counter() - t1) * 10_000  # 100 → avg per call in µs

    print(f"\n[WEB-005 perf] cold={cold_us:.1f}µs warm_avg={warm_avg_us:.1f}µs")
    # Warm path doesn't even re-stat (TTL>0); should be sub-microsecond on any CI.
    assert warm_avg_us < cold_us, (
        f"Warm path should be faster than cold creation; "
        f"cold={cold_us:.1f}µs warm={warm_avg_us:.1f}µs"
    )
