"""Миграция 0047: зависимые представления переживают пересборку таблиц.

Пересборка снимает все представления и создаёт их заново — в порядке создания
(``rowid`` в ``sqlite_master``). Ревью PR #193 опасалось, что при порядке по
имени ``aa_dep`` поверх ``zz_base`` упадёт с ``no such table``. На SQLite это не
так: ``CREATE VIEW`` не разрешает ссылки при создании, только при чтении
(проверено на 3.53), поэтому тест проходит при любом порядке. Он стережёт
результат — оба представления на месте и читаются после миграции, — а порядок
создания оставлен как гигиена на случай движка, который проверяет ссылки сразу.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

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


def test_dependent_views_survive_rebuild(tmp_path: Path) -> None:
    db_path = tmp_path / "views.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _previous_revision(), db_url=db_url)
    conn = sqlite3.connect(db_path)
    try:
        # Зависимое представление нарочно раньше по алфавиту, чем его основа.
        conn.execute("CREATE VIEW zz_base AS SELECT row_id, scope FROM plan")
        conn.execute("CREATE VIEW aa_dep AS SELECT scope FROM zz_base")
        conn.commit()
    finally:
        conn.close()

    run_alembic("upgrade", _THIS, db_url=db_url)

    conn = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        # plan_totals создан задолго до 0047 и читает plan — его 0047 снимает и
        # восстанавливает вместе с тестовыми; проверяем, что и он пережил пересборку.
        assert {"aa_dep", "zz_base", "plan_totals"} <= names
        assert conn.execute("SELECT count(*) FROM aa_dep").fetchone() == (0,)
    finally:
        conn.close()
