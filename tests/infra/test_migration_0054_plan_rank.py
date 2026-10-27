"""ROAD-001: миграция 0054 — ``plan.rank``, порядок планов проекта (RFC 36 §3.2.2).

Что тест обязан закрыть:

1. Бэкфилл внутри каждого проекта повторяет порядок, которым планы отдавал
   ``plan_service.list_for_project`` до 0054: ``created``, затем ``row_id``.
   Сид нарочно расходит ``created`` с ``row_id`` и кладёт два проекта —
   на «планы по возрастанию row_id в одном проекте» любой бэкфилл верен.
2. ``ADD COLUMN`` без пересборки: секции, задачи, рёбра и остальные строки с
   CASCADE-родителями целы (на SQLite пересборка ``plan`` унесла бы их).
3. Downgrade возвращает схему 0053 и не теряет данных.
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
    _insert,
    _schema,
    _seed,
)

if TYPE_CHECKING:
    from pathlib import Path

_THIS = "0054_plan_rank"
_PREVIOUS = _down_revision(_THIS)

#: Проект → ожидаемый порядок row_id планов. Литерал из сида, а не тот же SQL,
#: что у миграции.
_EXPECTED: dict[int, list[int]] = {1: [3, 1, 4, 2], 2: [6, 5]}


def _seed_plans(db_path: Path) -> None:
    """Планы сверх сида 0049: ``created`` не по ``row_id``, ничья по ``created``."""
    conn = sqlite3.connect(db_path)
    try:
        _insert(
            conn,
            "project",
            row_id=2,
            slug="p2",
            title="P2",
            root_path="/tmp/p2",
            created="2026-01-01 00:00:00",
            updated="2026-01-01 00:00:00",
            config_json="{}",
        )
        # План row_id=1 из сида 0049 создан «сейчас» — позже всех ниже, кроме
        # 2 и 4: 4 делит с 1 created и проигрывает по row_id.
        conn.execute("UPDATE plan SET created = '2026-05-01 00:00:00' WHERE row_id = 1")
        for row_id, project_id, created in (
            (2, 1, "2026-09-01 00:00:00"),
            (3, 1, "2026-02-01 00:00:00"),
            (4, 1, "2026-05-01 00:00:00"),
            (5, 2, "2026-07-01 00:00:00"),
            (6, 2, "2026-03-01 00:00:00"),
        ):
            _insert(
                conn,
                "plan",
                row_id=row_id,
                project_id=project_id,
                scope=f"plan-{row_id}",
                created=created,
                last_updated=created,
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def upgraded(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    db_path = tmp_path / "seeded.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    _seed_plans(db_path)
    seeded = _counts(db_path)
    run_alembic("upgrade", _THIS, db_url=db_url)
    return db_path, seeded


def _order(db_path: Path, project_id: int) -> list[int]:
    conn = sqlite3.connect(db_path)
    try:
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT row_id FROM plan WHERE project_id = ? ORDER BY rank, uid, row_id",
                (project_id,),
            )
        ]
    finally:
        conn.close()


def test_backfill_keeps_the_created_order_per_project(
    upgraded: tuple[Path, dict[str, int]],
) -> None:
    db_path, _seeded = upgraded
    assert {project: _order(db_path, project) for project in _EXPECTED} == _EXPECTED


def test_every_plan_gets_a_rank(upgraded: tuple[Path, dict[str, int]]) -> None:
    db_path, _seeded = upgraded
    conn = sqlite3.connect(db_path)
    try:
        missing = conn.execute("SELECT count(*) FROM plan WHERE rank IS NULL").fetchone()[0]
    finally:
        conn.close()
    assert missing == 0


def test_upgrade_keeps_rows(upgraded: tuple[Path, dict[str, int]]) -> None:
    db_path, seeded = upgraded
    assert _counts(db_path) == seeded
    assert _fk_violations(db_path) == []


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
