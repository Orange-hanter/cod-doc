"""Изоляция COD-DOC state для api-тестов.

Гарантирует:
- Записи о проектах не утекают в `~/.cod-doc/config.yaml` (изоляция
  через monkeypatch `CONFIG_DIR` / `CONFIG_FILE`).
- Каждый тест стартует с пустым `cod_doc.api.deps._ENGINE_CACHE`
  (после WEB-005 кэш живёт на уровне модуля и иначе утечёт между
  тестами engine-ссылками на удалённые `tmp_path` директории).
- Общий `migrate_db` fixture для применения alembic-миграций к
  embedded SQLite (раньше дублировался по тест-файлам — WEB-053).

⚠ Lifespan vs. set_config footgun:
The FastAPI app lifespan calls `Config.load()` and OVERWRITES whatever
the test pre-set via `deps.set_config(cfg)`. Tests that need a specific
config visible to handlers must call `cfg.save()` BEFORE entering the
TestClient (since CONFIG_FILE is monkeypatched into tmp_path, this is
local). Then access the live config via `deps.get_config()` after entry.
See `tests/api/test_web_settings.py:settings_client` for the pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._alembic import run_alembic_upgrade

REPO_ROOT = Path(__file__).resolve().parents[2]


# WEB-053b: single source of truth for project-tab expectations.
# All currently-rendered tabs ("Agent" included) are live.
EXPECTED_LIVE_TABS: tuple[str, ...] = ("overview", "run", "docs", "tasks", "plans", "revisions")
EXPECTED_DISABLED_TABS: tuple[str, ...] = ()


def assert_active_tab(html: str, slug: str, section: str) -> None:
    """Assert *section* tab is active in the project tab strip."""
    import re

    href = f"/p/{slug}" if section == "overview" else f"/p/{slug}/{section}"
    pattern = (
        rf'<a[^>]*class="[^"]*tab-link[^"]*active[^"]*"[^>]*href="{re.escape(href)}"'
        rf'|<a[^>]*href="{re.escape(href)}"[^>]*class="[^"]*tab-link[^"]*active[^"]*"'
    )
    assert re.search(pattern, html), f"active tab for {section!r} not found"


@pytest.fixture(autouse=True)
def _web_ui_english_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stable English copy in web template smoke tests."""
    monkeypatch.setenv("COD_DOC_LOCALE", "en")


@pytest.fixture
def migrate_db():
    """Apply Alembic migrations to a sqlite file, used to seed test DBs.

    Returns a function `(db_path: Path) -> None` so callers can build
    their own URL: `migrate_db(db_path)` runs `alembic upgrade head`
    against `sqlite:///<db_path>`.
    """

    def _apply(db_path: Path) -> None:
        run_alembic_upgrade(f"sqlite:///{db_path}")

    return _apply


@pytest.fixture(autouse=True)
def isolated_cod_doc_home(tmp_path: Path, monkeypatch):
    home = tmp_path / "cod-doc-home"
    home.mkdir()
    monkeypatch.setattr("cod_doc.config.CONFIG_DIR", home)
    monkeypatch.setattr("cod_doc.config.CONFIG_FILE", home / "config.yaml")
    yield home


@pytest.fixture(autouse=True)
def _isolated_engine_cache():
    """Reset the per-project DB engine cache between API tests.

    Module-level state in `cod_doc.api.deps` persists across tests inside
    the same pytest process. Without this fixture, tests that don't
    actively dispose leak Engine handles to deleted tmp_path directories
    — cumulative FD/memory pressure in long suites and harder-to-debug
    test interactions if a path is ever reused.
    """
    from cod_doc.api.deps import dispose_all_engines

    dispose_all_engines()
    yield
    dispose_all_engines()
