"""CLO-003: миграция 0050 — дробный ранг в 11 местах порядка.

Что тест обязан закрыть (RFC 29 §3.2, §4, §10.1, §11.1):

1. Порядок внутри каждого родителя после бэкфилла совпадает с тем, что
   показывал код до 0050: ``position``, затем тай-брейк чтения. Данные
   наливаются на предыдущей ревизии с перемешанными позициями и ничьими —
   на «0, 1, 2 в порядке row_id» любой бэкфилл выглядел бы верным.
2. Четыре ``UNIQUE (…, position)`` сняты, у пересобираемых таблиц нет
   CASCADE-детей (проверяется на схеме до миграции), строки и внешние ключи
   целы.
3. Downgrade возвращает UNIQUE и схему предыдущей ревизии и отказывает, пока
   позиции повторяются.
"""

from __future__ import annotations

import sqlite3
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests._alembic import run_alembic
from tests.infra.test_migration_0049_preserves_data import (
    _counts,
    _down_revision,
    _fk_violations,
    _ident,
    _insert,
    _now,
    _schema,
    _seed,
)

if TYPE_CHECKING:
    from pathlib import Path

_THIS = "0050_sync_rank"
_PREVIOUS = _down_revision(_THIS)

_REBUILT = {
    "scenario_step": "uq_scenario_step_position",
    "story_acceptance": "uq_story_acceptance_position",
    "adr_diagram": "uq_adr_diagram_position",
    "open_question_option": "uq_open_question_option_position",
}

#: (таблица, колонка ранга, условие родителя) → ожидаемый порядок row_id.
#: Ожидание — литерал из сида, а не тот же SQL, что у миграции.
_EXPECTED: tuple[tuple[str, str, str, list[int]], ...] = (
    ("section", "rank", "document_id = 1", [3, 1, 2, 4]),
    ("doc_node", "rank", "project_id = 1", [2, 1, 3]),
    ("plan_section", "rank", "plan_id = 1", [3, 1, 2]),
    ("scenario", "rank", "project_id = 1 AND group_key = 'g'", [1, 3, 2]),
    ("scenario", "rank", "project_id = 1 AND group_key = 'h'", [4]),
    ("scenario_step", "rank", "scenario_row_id = 1", [1, 3, 2]),
    ("story_section", "rank", "project_id = 1", [3, 2, 1]),
    ("story_acceptance", "rank", "story_id = 1", [1, 3, 2]),
    ("adr_diagram", "rank", "adr_id = 1", [2, 1]),
    ("adr_diagram", "rank", "adr_id = 2", [3]),
    ("adr_topic", "rank", "project_id = 1", [3, 1, 2]),
    ("open_question_option", "rank", "question_row_id = 1", [1, 3, 2]),
    ("document", "node_rank", "node_id = 1", [2, 3, 1]),
)


def _seed_order(db_path: Path) -> None:
    """Вторые и третьи строки в каждом месте порядка: дыры, ничьи, отрицательные."""
    now = _now()
    conn = sqlite3.connect(db_path)
    try:
        for row_id, position in ((3, 0), (4, 2)):
            _insert(
                conn,
                "section",
                row_id=row_id,
                document_id=1,
                anchor=f"s{row_id}",
                heading="h",
                level=2,
                position=position,
                body="b",
                content_hash="h",
            )
        for row_id, key, position in ((2, "aaa", 0), (3, "z", 5)):
            _insert(
                conn, "doc_node", row_id=row_id, project_id=1, node_key=key, title=key,
                position=position,
            )  # fmt: skip
        for row_id, letter, position in ((2, "B", 0), (3, "C", -1)):
            _insert(
                conn, "plan_section", row_id=row_id, plan_id=1, letter=letter, title=letter,
                slug=letter, position=position,
            )  # fmt: skip
        for row_id, sid, group in ((2, "SCN-003", "g"), (3, "SCN-002", "g"), (4, "SCN-004", "h")):
            _insert(
                conn, "scenario", row_id=row_id, project_id=1, scenario_id=sid, title="s",
                kind="happy", group_key=group, status="draft", provenance="human",
                preconditions="p", expected="e", position=0, author="human:t",
                created=now, last_updated=now,
            )  # fmt: skip
        for row_id, position in ((2, 2), (3, 1)):
            _insert(conn, "scenario_step", row_id=row_id, scenario_row_id=1, position=position,
                    text="t")  # fmt: skip
        for row_id, key, position in ((1, "b", 0), (2, "a", 0), (3, "c", -1)):
            _insert(conn, "story_section", row_id=row_id, project_id=1, key=key, title=key,
                    position=position)  # fmt: skip
        for row_id, position in ((2, 3), (3, 1)):
            _insert(conn, "story_acceptance", row_id=row_id, story_id=1, position=position,
                    criterion="c")  # fmt: skip
        for row_id, adr, position in ((1, 1, 1), (2, 1, 0), (3, 2, 0)):
            _insert(conn, "adr_diagram", row_id=row_id, adr_id=adr, position=position,
                    mermaid="graph")  # fmt: skip
        for row_id, name, position in ((2, "u", 0), (3, "v", -1)):
            _insert(conn, "adr_topic", row_id=row_id, project_id=1, name=name, position=position,
                    created=now, last_updated=now)  # fmt: skip
        for row_id, position in ((2, 5), (3, 2)):
            _insert(conn, "open_question_option", row_id=row_id, question_row_id=1,
                    position=position, title="o", chosen=0)  # fmt: skip
        for row_id, key, node, node_position in (
            (2, "zeta", 1, 0),
            (3, "alpha", 1, None),
            (4, "loose", None, None),
        ):
            _insert(
                conn, "document", row_id=row_id, project_id=1, doc_key=key, path=f"{key}.md",
                type="guide", status="active", title=key, created=now, last_updated=now,
                node_id=node, node_position=node_position,
            )  # fmt: skip
        conn.commit()
    finally:
        conn.close()


def _seeded_db(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    db_path = tmp_path / "seeded.db"
    run_alembic("upgrade", _PREVIOUS, db_url=f"sqlite:///{db_path}")
    _seed(db_path)
    _seed_order(db_path)
    return db_path, _counts(db_path)


@pytest.fixture
def upgraded(tmp_path: Path) -> tuple[Path, dict[str, int]]:
    db_path, seeded = _seeded_db(tmp_path)
    run_alembic("upgrade", _THIS, db_url=f"sqlite:///{db_path}")
    return db_path, seeded


def _ranked(conn: sqlite3.Connection, table: str, column: str, where: str) -> list[int]:
    return [
        row[0]
        for row in conn.execute(
            f"SELECT row_id FROM {_ident(table)} WHERE {where} "
            f"ORDER BY {_ident(column)}, uid, row_id"
        )
    ]


def test_rank_backfill_keeps_order_inside_each_parent(
    upgraded: tuple[Path, dict[str, int]],
) -> None:
    db_path, _ = upgraded
    conn = sqlite3.connect(db_path)
    try:
        for table, column, where, expected in _EXPECTED:
            assert _ranked(conn, table, column, where) == expected, (table, where)
            ranks = [
                r[0] for r in conn.execute(f"SELECT {_ident(column)} FROM {_ident(table)} "
                                           f"WHERE {where}")
            ]  # fmt: skip
            assert all(ranks), (table, "строка без ранга")
            assert len(set(ranks)) == len(ranks), (table, "ранги повторяются внутри родителя")
        unplaced = conn.execute("SELECT node_rank FROM document WHERE row_id = 4").fetchone()
        assert unplaced == (None,), "неразложенный документ не упорядочен"
    finally:
        conn.close()


def test_upgrade_keeps_rows_and_drops_position_uniques(
    upgraded: tuple[Path, dict[str, int]],
) -> None:
    db_path, seeded = upgraded
    assert _counts(db_path) == seeded
    assert _fk_violations(db_path) == []
    conn = sqlite3.connect(db_path)
    try:
        for table, constraint in _REBUILT.items():
            (sql,) = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            assert constraint not in sql, table
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
                (f"ix_{table}_position",),
            ).fetchone(), f"{table}: поиск по позиции без индекса"
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
                (f"uq_{table}_uid",),
            ).fetchone(), f"{table}: индекс uid потерян при пересборке"
        # Позиция больше не уникальна: две реплики законно делят одну до пересчёта.
        conn.execute(
            "INSERT INTO scenario_step (scenario_row_id, position, text) VALUES (1, 0, 'dup')"
        )
    finally:
        conn.close()


def test_rebuilt_tables_have_no_cascade_children(tmp_path: Path) -> None:
    """Пересоздание безопасно, только пока на таблицы никто не ссылается (RFC §4).

    Проверяется на схеме *до* миграции: новая таблица с FK на ``scenario_step``
    уронит этот тест раньше, чем миграцию на живой БД.
    """
    db_path = tmp_path / "prev.db"
    run_alembic("upgrade", _PREVIOUS, db_url=f"sqlite:///{db_path}")
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        children = [
            (table, fk[2])
            for table in tables
            for fk in conn.execute(f"PRAGMA foreign_key_list({_ident(table)})")
            if fk[2] in _REBUILT
        ]
    finally:
        conn.close()
    assert children == []


def _unquoted(schema: set[tuple[str, frozenset[str]]]) -> set[tuple[str, frozenset[str]]]:
    """``ALTER TABLE … RENAME`` пишет имя в кавычках; содержательно это то же имя."""
    return {(name, frozenset(p.replace('"', "") for p in parts)) for name, parts in schema}


def test_downgrade_restores_uniques_and_schema(tmp_path: Path) -> None:
    db_path, seeded = _seeded_db(tmp_path)
    before = _schema(db_path)
    url = f"sqlite:///{db_path}"
    run_alembic("upgrade", _THIS, db_url=url)
    run_alembic("downgrade", _PREVIOUS, db_url=url)
    assert _unquoted(_schema(db_path)) == _unquoted(before)
    assert _counts(db_path) == seeded
    assert _fk_violations(db_path) == []


def test_downgrade_refuses_duplicate_positions(upgraded: tuple[Path, dict[str, int]]) -> None:
    db_path, _ = upgraded
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO story_acceptance (story_id, position, criterion) VALUES (1, 0, 'dup')"
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_alembic("downgrade", _PREVIOUS, db_url=f"sqlite:///{db_path}")
    assert b"duplicate (story_id, position)" in failure.value.stderr
    conn = sqlite3.connect(db_path)
    try:
        (version,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    finally:
        conn.close()
    assert version == _THIS, "отказ должен случиться до изменений"
