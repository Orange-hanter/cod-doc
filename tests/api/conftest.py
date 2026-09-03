"""Shared fixtures/constants for API tests.

Global COD-DOC home / API runtime isolation lives in ``tests/conftest.py`` so it
also covers top-level API integration tests. This file keeps web/API-specific
helpers such as Alembic migration setup and tab expectations.

⚠ Lifespan vs. set_config footgun:
The FastAPI app lifespan calls `Config.load()` and OVERWRITES whatever
the test pre-set via `deps.set_config(cfg)`. Tests that need a specific
config visible to handlers must call `cfg.save()` BEFORE entering the
TestClient (since CONFIG_FILE is monkeypatched into tmp_path, this is
local). Then access the live config via `deps.get_config()` after entry.
See `tests/api/test_web_settings.py:settings_client` for the pattern.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

# WEB-053b: single source of truth for project-tab expectations.
# All currently-rendered tabs ("Agent" included) are live.
EXPECTED_LIVE_TABS: tuple[str, ...] = ("overview", "run", "docs", "tasks", "plans", "revisions")
EXPECTED_DISABLED_TABS: tuple[str, ...] = ()


@pytest.fixture
def migrate_db():
    """Apply Alembic migrations to a sqlite file, used to seed test DBs.

    Returns a function `(db_path: Path) -> None` so callers can build
    their own URL: `migrate_db(db_path)` runs `alembic upgrade head`
    against `sqlite:///<db_path>`.
    """

    def _apply(db_path: Path) -> None:
        run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    return _apply
