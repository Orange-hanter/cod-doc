"""ARG-008: миграция 0046 не теряет данные ADR — ни вверх, ни вниз.

``adr`` держит по ``ON DELETE CASCADE`` диаграммы, связи с задачами, замены и
связи ARG-001. Пересоздание таблицы (``batch_alter_table`` на SQLite) унесло
бы их все — ровно так 0035 стоила живой БД 1379 секций (ADO-116). 0046
добавляет и снимает колонку ``adr.topic_id`` нативным ``ALTER``; тест наливает
данные на предыдущей ревизии и считает их после upgrade и после downgrade.
На пустой БД такая потеря не видна.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

_PREVIOUS = "0045_adr_relations"
_TABLES = ("adr", "adr_diagram", "adr_task", "adr_supersedes", "adr_relation")


def _seed(db_path: Path) -> None:
    now = datetime.now(UTC).isoformat(sep=" ")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO project (row_id, slug, title, root_path, created, updated, config_json)"
            " VALUES (1, 'p', 'P', '/tmp/p', ?, ?, '{}')",
            (now, now),
        )
        for row_id, adr_id, status in ((1, "ADR-001", "superseded"), (2, "ADR-002", "accepted")):
            conn.execute(
                "INSERT INTO adr (row_id, project_id, adr_id, title, status, author,"
                " created, last_updated) VALUES (?, 1, ?, 'T', ?, 'human', ?, ?)",
                (row_id, adr_id, status, now, now),
            )
        conn.execute(
            "INSERT INTO adr_diagram (row_id, adr_id, position, mermaid) VALUES (1, 2, 0, 'graph')"
        )
        conn.execute(
            "INSERT INTO adr_task (row_id, adr_row_id, task_id, relation)"
            " VALUES (1, 2, 'ADO-1', 'implements')"
        )
        conn.execute(
            "INSERT INTO adr_supersedes (row_id, superseding_id, superseded_id, at)"
            " VALUES (1, 2, 1, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO adr_relation (row_id, from_id, to_id, kind, at)"
            " VALUES (1, 2, 1, 'amends', ?)",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()


def _counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {t: int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]) for t in _TABLES}
    finally:
        conn.close()


def test_0046_keeps_adr_children_up_and_down(tmp_path: Path) -> None:
    db_path = tmp_path / "seeded.db"
    db_url = f"sqlite:///{db_path}"
    expected = {t: (2 if t == "adr" else 1) for t in _TABLES}

    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    assert _counts(db_path) == expected

    run_alembic("upgrade", "head", db_url=db_url)
    assert _counts(db_path) == expected

    run_alembic("downgrade", _PREVIOUS, db_url=db_url)
    assert _counts(db_path) == expected
