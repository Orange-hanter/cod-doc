"""ADO-116: миграция 0035 не имеет права терять секции и ссылки.

Первая версия миграции добавляла ``document.node_id`` через
``batch_alter_table``. На SQLite это пересоздание таблицы: копия во временную,
DROP старой, RENAME. При включённом ``PRAGMA foreign_keys`` DROP таблицы
``document`` уносит по ``ON DELETE CASCADE`` все её ``section``, а вместе с
ними — все ``link``, которые висят на секциях.

На живой БД репозитория это стоило 1379 секций и 841 ссылки при сохранённых
170 документах. Обычный прогон миграционных тестов этого не видел: тестовая БД
пуста, и терять в ней нечего.

Поэтому тест наливает данные на предыдущей ревизии и проверяет их после
upgrade — единственная форма, в которой такая потеря вообще заметна.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

_PREVIOUS = "0034_story_section"


def _seed(db_path: Path) -> None:
    """Проект, документ, секция и ссылка — цепочка, которую рвал CASCADE."""
    now = datetime.now(UTC).isoformat(sep=" ")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO project (row_id, slug, title, root_path, created, updated, config_json)"
            " VALUES (1, 'p', 'P', '/tmp/p', ?, ?, '{}')",
            (now, now),
        )
        conn.execute(
            "INSERT INTO document (row_id, project_id, doc_key, path, type, status,"
            " source_of_truth, sensitivity, title, preamble, frontmatter_json,"
            " created, last_updated)"
            " VALUES (1, 1, 'a/b', 'a/b.md', 'module-spec', 'draft', 1, 'internal',"
            " 'A', '', '{}', ?, ?)",
            (now, now),
        )
        conn.execute(
            "INSERT INTO section (row_id, document_id, anchor, heading, level, position,"
            " body, content_hash) VALUES (1, 1, 'x', 'X', 2, 0, 'body', 'h')"
        )
        conn.execute(
            "INSERT INTO link (row_id, project_id, from_section_id, raw, kind, to_doc_key,"
            " resolved) VALUES (1, 1, 1, '[x](a/b.md)', 'markdown', 'a/b', 1)"
        )
        conn.commit()
    finally:
        conn.close()


def _counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("document", "section", "link")
        }
    finally:
        conn.close()


def test_upgrade_keeps_sections_and_links(tmp_path: Path) -> None:
    db_path = tmp_path / "seeded.db"
    db_url = f"sqlite:///{db_path}"

    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    assert _counts(db_path) == {"document": 1, "section": 1, "link": 1}

    run_alembic("upgrade", "head", db_url=db_url)
    assert _counts(db_path) == {"document": 1, "section": 1, "link": 1}, (
        "миграция 0035 потеряла секции или ссылки: перестройка `document` на "
        "SQLite уносит их по ON DELETE CASCADE"
    )


def test_document_body_view_still_works_after_upgrade(tmp_path: Path) -> None:
    """View ``document_body`` ссылается на ``document``; от него считается
    ``projection_hash``, поэтому его поломка испортила бы хэши всего корпуса."""
    db_path = tmp_path / "seeded-view.db"
    db_url = f"sqlite:///{db_path}"

    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    run_alembic("upgrade", "head", db_url=db_url)

    conn = sqlite3.connect(db_path)
    try:
        body = conn.execute("SELECT body FROM document_body WHERE document_id = 1").fetchone()
    finally:
        conn.close()
    assert body is not None
    assert "## X" in body[0]


def test_downgrade_is_symmetric_and_keeps_data(tmp_path: Path) -> None:
    db_path = tmp_path / "seeded-down.db"
    db_url = f"sqlite:///{db_path}"

    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    run_alembic("upgrade", "head", db_url=db_url)
    run_alembic("downgrade", _PREVIOUS, db_url=db_url)

    assert _counts(db_path) == {"document": 1, "section": 1, "link": 1}
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(document)")}
    finally:
        conn.close()
    assert "node_id" not in columns
    assert "node_position" not in columns
