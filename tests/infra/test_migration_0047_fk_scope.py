"""Миграция 0047 проверяет внешние ключи только там, где могла их задеть.

После пересборки ``plan``, ``user_story`` и ``module`` миграция зовёт
``PRAGMA foreign_key_check``. По всей БД эта проверка остановила бы миграцию на
давнем нарушении в посторонней таблице (hub, ручная правка) — к 0047 оно не
относится. Поэтому проверяются три таблицы и их дети, и нарушение у ребёнка
по-прежнему останавливает миграцию.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests._alembic import run_alembic

_THIS = "0047_project_scoped_uniques"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _previous_revision() -> str:
    script = ScriptDirectory.from_config(Config(str(_REPO_ROOT / "alembic.ini")))
    down = script.get_revision(_THIS).down_revision
    assert isinstance(down, str)
    return down


def _db_at_previous(tmp_path: Path) -> tuple[Path, str]:
    db_path = tmp_path / "fk.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _previous_revision(), db_url=db_url)
    return db_path, db_url


def _insert_orphan(db_path: Path, sql: str, params: tuple[object, ...]) -> None:
    """Сирота при выключенных внешних ключах — как после ручной правки БД."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def test_unrelated_legacy_violation_does_not_block(tmp_path: Path) -> None:
    db_path, db_url = _db_at_previous(tmp_path)
    # tag ссылается на project, а не на пересобираемые таблицы.
    _insert_orphan(db_path, "INSERT INTO tag (row_id, project_id, name) VALUES (1, 999, 'x')", ())

    run_alembic("upgrade", _THIS, db_url=db_url)

    conn = sqlite3.connect(db_path)
    try:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'plan'").fetchone()[0]
    finally:
        conn.close()
    assert "UNIQUE (project_id, scope)" in sql


def test_violation_in_child_of_rebuilt_table_blocks(tmp_path: Path) -> None:
    db_path, db_url = _db_at_previous(tmp_path)
    # plan_section — ребёнок plan; план 999 не существует.
    _insert_orphan(
        db_path,
        "INSERT INTO plan_section (row_id, plan_id, letter, title, slug, position)"
        " VALUES (1, 999, 'A', 'T', 'A-T', 0)",
        (),
    )

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_alembic("upgrade", _THIS, db_url=db_url)
    stderr = (excinfo.value.stderr or b"").decode("utf-8", "replace")
    assert "нарушены внешние ключи" in stderr
    assert "plan_section" in stderr
