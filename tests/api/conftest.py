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

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd_base = [str(venv_alembic) if venv_alembic.exists() else "alembic"]

    def _apply(db_path: Path) -> None:
        subprocess.run(
            [*cmd_base, "upgrade", "head"],
            cwd=REPO_ROOT,
            check=True,
            env={"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": f"sqlite:///{db_path}"},
            capture_output=True,
        )

    return _apply
