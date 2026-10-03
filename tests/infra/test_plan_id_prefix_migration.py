"""0043: колонка `plan.id_prefix` — и данные плана, переживающие обе стороны.

Интересно здесь не «колонка появилась», а **что downgrade не трогает
задачи**. У `plan` два каскадных потомка — `plan_section` и `task`
(`ON DELETE CASCADE`). Удаление колонки через `batch_alter_table` на SQLite
пересоздаёт таблицу, а DROP старой уносит по каскаду всё, что на ней висит;
на пустой тестовой БД такая миграция зеленеет. Поэтому план с секцией и
задачей наливается сырым SQL на 0042 и пересчитывается после каждого шага.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import inspect, text

from cod_doc.infra.db import make_engine
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

BEFORE = "0042_open_questions"


@pytest.fixture
def seeded(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'prefix.db'}"
    run_alembic("upgrade", BEFORE, db_url=url)
    now = datetime.now(UTC).isoformat()
    engine = make_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO project (slug, title, root_path, config_json, created, updated) "
                    "VALUES ('p', 'P', '/tmp/p', '{}', :now, :now)"
                ),
                {"now": now},
            )
            conn.execute(
                text(
                    "INSERT INTO plan (project_id, scope, created, last_updated) "
                    "VALUES (1, 'p-plan', :now, :now)"
                ),
                {"now": now},
            )
            conn.execute(
                text(
                    "INSERT INTO plan_section (plan_id, letter, title, slug, position) "
                    "VALUES (1, 'A', 'Sec', 'A-Sec', 0)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO task (project_id, task_id, plan_id, section_id, title, status, "
                    "type, priority, created, last_updated) VALUES (1, 'P-001', 1, 1, "
                    "'Implement: x', 'todo', 'feature', 'medium', :now, :now)"
                ),
                {"now": now},
            )
    finally:
        engine.dispose()
    return url


def _counts(url: str) -> tuple[int, int, int]:
    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            return (
                conn.execute(text("SELECT count(*) FROM plan")).scalar_one(),
                conn.execute(text("SELECT count(*) FROM plan_section")).scalar_one(),
                conn.execute(text("SELECT count(*) FROM task")).scalar_one(),
            )
    finally:
        engine.dispose()


def _columns(url: str) -> set[str]:
    engine = make_engine(url)
    try:
        return {c["name"] for c in inspect(engine).get_columns("plan")}
    finally:
        engine.dispose()


def test_upgrade_adds_the_column_and_keeps_rows(seeded: str) -> None:
    run_alembic("upgrade", "head", db_url=seeded)
    assert "id_prefix" in _columns(seeded)
    assert _counts(seeded) == (1, 1, 1)


def test_downgrade_drops_the_column_without_cascading(seeded: str) -> None:
    run_alembic("upgrade", "head", db_url=seeded)
    run_alembic("downgrade", BEFORE, db_url=seeded)
    assert "id_prefix" not in _columns(seeded)
    assert _counts(seeded) == (1, 1, 1), "downgrade унёс секции или задачи по каскаду"
