"""Shared fixtures for service-layer tests.

Every test_*_service.py module in this directory used to redefine these
three fixtures locally. Centralising them here removes ~25 LOC × 9 files
of duplicated boilerplate without changing test behaviour — the SQLite
DB file lives in the test's `tmp_path`, so each test still gets a fresh
schema via `alembic upgrade head`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.db import make_engine
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


def _run_alembic_upgrade(db_url: str) -> None:
    run_alembic("upgrade", "head", db_url=db_url)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()
