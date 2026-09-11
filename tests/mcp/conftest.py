"""Fixtures for MCP tool tests.

``engine_with_schema`` is duplicated from ``tests/services/conftest.py``
rather than pulled in via ``pytest_plugins`` — collecting both directories
in one pytest run would double-register that module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.db import make_engine
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()
