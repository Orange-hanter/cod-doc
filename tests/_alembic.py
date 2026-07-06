"""Shared alembic upgrade helper for test DB setup."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_alembic_upgrade(db_url: str) -> None:
    """Apply migrations to *db_url* via ``python -m alembic upgrade head``."""
    env = os.environ.copy()
    env["COD_DOC_DB_URL"] = db_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        check=True,
        env=env,
        capture_output=True,
    )


def run_alembic_downgrade(db_url: str, target: str) -> None:
    """Downgrade *db_url* to revision *target*."""
    env = os.environ.copy()
    env["COD_DOC_DB_URL"] = db_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", target],
        cwd=REPO_ROOT,
        check=True,
        env=env,
        capture_output=True,
    )
