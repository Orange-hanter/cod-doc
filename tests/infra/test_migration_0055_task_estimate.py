"""ROAD-009: миграция 0055 — ``task.estimate_person_hours`` (RFC 36 §3.2.3).

``ADD COLUMN`` без пересборки ``task``: задачи, рёбра, файлы и документы задач
целы; новая колонка пуста; downgrade возвращает схему 0054 без потерь.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from tests._alembic import run_alembic
from tests.infra.test_migration_0049_preserves_data import (
    _counts,
    _down_revision,
    _fk_violations,
    _schema,
    _seed,
)

if TYPE_CHECKING:
    from pathlib import Path

_THIS = "0055_task_estimate"
_PREVIOUS = _down_revision(_THIS)


@pytest.fixture
def upgraded(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    db_path = tmp_path / "seeded.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    seeded = _seed(db_path)
    run_alembic("upgrade", _THIS, db_url=db_url)
    return db_path, seeded


def test_upgrade_keeps_rows_and_column_is_empty(upgraded: tuple[Path, dict[str, int]]) -> None:
    db_path, seeded = upgraded
    assert _counts(db_path) == seeded
    assert _fk_violations(db_path) == []
    conn = sqlite3.connect(db_path)
    try:
        filled = conn.execute(
            "SELECT count(*) FROM task WHERE estimate_person_hours IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert filled == 0


def test_downgrade_is_symmetric_and_keeps_data(
    upgraded: tuple[Path, dict[str, int]], tmp_path: Path
) -> None:
    db_path, seeded = upgraded
    reference = tmp_path / "reference.db"
    run_alembic("upgrade", _PREVIOUS, db_url=f"sqlite:///{reference}")

    run_alembic("downgrade", _PREVIOUS, db_url=f"sqlite:///{db_path}")

    assert _counts(db_path) == seeded
    assert _fk_violations(db_path) == []
    assert _schema(db_path) == _schema(reference)
