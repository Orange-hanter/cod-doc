"""STO-018, STO-024: миграция 0047 — натуральные ключи уникальны в проекте.

Два вопроса, которые тест обязан закрыть:

1. Ключи действительно стали проектными: два проекта с одним ``plan.scope``,
   ``user_story.story_id`` и ``module.module_id`` уживаются в одной БД, дубль
   внутри проекта по-прежнему отвергается, а до миграции тот же сценарий даёт
   ``IntegrityError``.
2. Пересборка таблиц ничего не теряет. У ``plan``, ``user_story`` и ``module``
   есть CASCADE-дети; если ``PRAGMA foreign_keys=OFF`` не сработает (а внутри
   открытой транзакции SQLite её молча игнорирует), ``DROP TABLE`` удалит их
   каскадом. На пустой БД такую потерю не видно, поэтому данные наливаются на
   предыдущей ревизии — и отдельно на ревизии раньше, чтобы 0046 и 0047 прошли
   одним прогоном alembic.
"""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

_PREVIOUS = "0046_adr_topics"
_EARLIER = "0045_adr_relations"
_THIS = "0047_project_scoped_uniques"

_CHILD_TABLES = (
    "plan",
    "plan_section",
    "task",
    "dependency",
    "user_story",
    "story_acceptance",
    "story_link",
    "tag",
    "story_tag",
    "module",
    "module_code",
    "module_dependency",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(sep=" ")


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_project(conn: sqlite3.Connection, row_id: int) -> None:
    now = _now()
    conn.execute(
        "INSERT INTO project (row_id, slug, title, root_path, created, updated, config_json)"
        " VALUES (?, ?, ?, ?, ?, ?, '{}')",
        (row_id, f"p{row_id}", f"P{row_id}", f"/tmp/p{row_id}", now, now),
    )


def _add_keys(conn: sqlite3.Connection, project_id: int, *, base: int) -> None:
    """План, история и модуль с одинаковыми именами в любом проекте."""
    now = _now()
    conn.execute(
        "INSERT INTO plan (row_id, project_id, scope, created, last_updated)"
        " VALUES (?, ?, 'cod-doc-bootstrap', ?, ?)",
        (base, project_id, now, now),
    )
    conn.execute(
        "INSERT INTO user_story (row_id, project_id, story_id, persona, narrative, status,"
        " priority, created, last_updated)"
        " VALUES (?, ?, 'US-001', 'p', 'n', 'draft', 'medium', ?, ?)",
        (base, project_id, now, now),
    )
    conn.execute(
        "INSERT INTO module (row_id, project_id, module_id, name, status)"
        " VALUES (?, ?, 'M1-auth', 'Auth', 'active')",
        (base, project_id),
    )


def _seed(db_path: Path) -> None:
    """Проект с ключами и по строке в каждой CASCADE-дочерней таблице."""
    now = _now()
    conn = _connect(db_path)
    try:
        _add_project(conn, 1)
        _add_keys(conn, 1, base=1)
        conn.execute(
            "INSERT INTO module (row_id, project_id, module_id, name, status)"
            " VALUES (2, 1, 'M2-core', 'Core', 'active')"
        )
        conn.execute(
            "INSERT INTO plan_section (row_id, plan_id, letter, title, slug, position)"
            " VALUES (1, 1, 'A', 'Core', 'A-Core', 0)"
        )
        for row_id, task_id in ((1, "COD-001"), (2, "COD-002")):
            conn.execute(
                "INSERT INTO task (row_id, project_id, task_id, plan_id, section_id, title,"
                " status, type, priority, created, last_updated)"
                " VALUES (?, 1, ?, 1, 1, ?, 'todo', 'feature', 'medium', ?, ?)",
                (row_id, task_id, task_id, now, now),
            )
        conn.execute(
            "INSERT INTO dependency (row_id, from_task_id, to_task_id, kind)"
            " VALUES (1, 2, 1, 'blocks')"
        )
        conn.execute(
            "INSERT INTO story_acceptance (row_id, story_id, position, criterion)"
            " VALUES (1, 1, 0, 'c')"
        )
        conn.execute(
            "INSERT INTO story_link (row_id, story_id, to_kind, to_ref, relation)"
            " VALUES (1, 1, 'task', 'COD-001', 'implements')"
        )
        conn.execute("INSERT INTO tag (row_id, project_id, name) VALUES (1, 1, 'hub')")
        conn.execute("INSERT INTO story_tag (story_id, tag_id) VALUES (1, 1)")
        conn.execute(
            "INSERT INTO module_code (row_id, module_id, kind, path)"
            " VALUES (1, 1, 'source', 'cod_doc/auth.py')"
        )
        conn.execute(
            "INSERT INTO module_dependency (row_id, from_module, to_module, reason)"
            " VALUES (1, 1, 2, 'r')"
        )
        conn.commit()
    finally:
        conn.close()


def _counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            t: int(conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]) for t in _CHILD_TABLES
        }
    finally:
        conn.close()


def _seeded_counts() -> dict[str, int]:
    return {t: 1 for t in _CHILD_TABLES} | {"task": 2, "module": 2}


def _fk_violations(db_path: Path) -> list[tuple[object, ...]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("seed_at", [_PREVIOUS, _EARLIER])
def test_upgrade_keeps_cascade_children(tmp_path: Path, seed_at: str) -> None:
    db_path = tmp_path / "seeded.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", seed_at, db_url=db_url)
    _seed(db_path)
    assert _counts(db_path) == _seeded_counts()

    run_alembic("upgrade", "head", db_url=db_url)

    assert _counts(db_path) == _seeded_counts()
    assert _fk_violations(db_path) == []


def test_cross_project_duplicates_rejected_before_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "before.db"
    run_alembic("upgrade", _PREVIOUS, db_url=f"sqlite:///{db_path}")
    conn = _connect(db_path)
    try:
        _add_project(conn, 1)
        _add_project(conn, 2)
        _add_keys(conn, 1, base=1)
        with pytest.raises(sqlite3.IntegrityError):
            _add_keys(conn, 2, base=2)
    finally:
        conn.close()


def test_cross_project_duplicates_allowed_after_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "after.db"
    run_alembic("upgrade", _THIS, db_url=f"sqlite:///{db_path}")
    conn = _connect(db_path)
    try:
        _add_project(conn, 1)
        _add_project(conn, 2)
        _add_keys(conn, 1, base=1)
        _add_keys(conn, 2, base=2)
        conn.commit()
        for table, column in (
            ("plan", "scope"),
            ("user_story", "story_id"),
            ("module", "module_id"),
        ):
            assert (
                conn.execute(
                    f"SELECT count(DISTINCT project_id) FROM {table} GROUP BY {column}"
                ).fetchone()[0]
                == 2
            )
        # Внутри проекта ключ по-прежнему уникален.
        with pytest.raises(sqlite3.IntegrityError):
            _add_keys(conn, 1, base=3)
    finally:
        conn.close()


def test_downgrade_is_symmetric_and_keeps_data(tmp_path: Path) -> None:
    db_path = tmp_path / "roundtrip.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    run_alembic("upgrade", _THIS, db_url=db_url)

    run_alembic("downgrade", _PREVIOUS, db_url=db_url)

    assert _counts(db_path) == _seeded_counts()
    assert _fk_violations(db_path) == []
    conn = _connect(db_path)
    try:
        _add_project(conn, 2)
        with pytest.raises(sqlite3.IntegrityError):
            _add_keys(conn, 2, base=2)
    finally:
        conn.close()


def test_downgrade_refuses_cross_project_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "dups.db"
    db_url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _THIS, db_url=db_url)
    conn = _connect(db_path)
    try:
        _add_project(conn, 1)
        _add_project(conn, 2)
        _add_keys(conn, 1, base=1)
        _add_keys(conn, 2, base=2)
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_alembic("downgrade", _PREVIOUS, db_url=db_url)
    assert b"shared across projects" in (excinfo.value.stderr or b"")
    assert _counts(db_path)["plan"] == 2
