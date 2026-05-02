"""Shared fixtures for service-layer tests.

Every test_*_service.py module in this directory used to redefine these
three fixtures locally. Centralising them here removes ~25 LOC × 9 files
of duplicated boilerplate without changing test behaviour — the SQLite
DB file lives in the test's `tmp_path`, so each test still gets a fresh
schema via `alembic upgrade head`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cod_doc.infra.db import make_engine

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()
