"""Миграция 0036 не имеет права терять данные и обязана дать нулевую серию.

Данные наливаются на предыдущей ревизии и проверяются после upgrade — на
пустой тестовой БД потеря не видна вовсе, терять там нечего.

**Чего этот тест НЕ проверяет**, чтобы на него не полагались сверх меры: он
не отличает прямой ``ADD COLUMN`` от ``batch_alter_table``. Напрашивается
обратное — на ``finding.row_id`` висит ``finding_source_run.finding_id`` с
``ON DELETE CASCADE``, и DROP при перестройке должен был бы унести журнал, —
но проверено замером: alembic снимает ``PRAGMA foreign_keys`` на время batch,
и обе формы миграции проходят этот файл одинаково зелёными. Выбор прямого DDL
в 0036 обоснован тем, что перестройка не нужна, а не тем, что она сломает
данные.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

_PREVIOUS = "0035_doc_nodes"


def _seed(db_path: Path) -> None:
    """Проект, находка и строка журнала — цепочка, которую рвал бы CASCADE."""
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
            "INSERT INTO finding (row_id, project_id, finding_uid, source, source_ref,"
            " fingerprint, severity, kind, title, status, first_seen_at, last_seen_at,"
            " times_seen, payload)"
            " VALUES (1, 1, 'uid-1', 'routine', 'doc_node_health_ai', 'fp-1', 'minor',"
            " 'NODE-INTENT-AI', 'Раздел не покрывает назначение', 'open', ?, ?, 3, '{}')",
            (now, now),
        )
        conn.execute(
            "INSERT INTO finding_source_run (row_id, finding_id, source_run_id, ts,"
            " severity_at_run) VALUES (1, 1, 'run-1', ?, 'minor')",
            (now,),
        )
        conn.execute(
            "INSERT INTO finding_source_run (row_id, finding_id, source_run_id, ts,"
            " severity_at_run) VALUES (2, 1, 'run-2', ?, 'minor')",
            (now,),
        )
        conn.commit()
    finally:
        conn.close()


def _counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        return {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in ("finding", "finding_source_run")
        }
    finally:
        conn.close()


def test_upgrade_keeps_the_source_run_journal(tmp_path: Path) -> None:
    db_path = tmp_path / "seeded.db"
    db_url = f"sqlite:///{db_path}"

    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    assert _counts(db_path) == {"finding": 1, "finding_source_run": 2}

    run_alembic("upgrade", "head", db_url=db_url)
    assert _counts(db_path) == {"finding": 1, "finding_source_run": 2}, (
        "миграция 0036 потеряла находку или её журнал прогонов"
    )


def test_existing_findings_start_with_a_zero_streak(tmp_path: Path) -> None:
    """Существующие строки обязаны вести себя ровно как до миграции."""
    db_path = tmp_path / "seeded-default.db"
    db_url = f"sqlite:///{db_path}"

    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    run_alembic("upgrade", "head", db_url=db_url)

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT miss_streak, times_seen, status FROM finding").fetchone()
    finally:
        conn.close()
    # `times_seen` не трогаем: это отметка о настоящем наблюдении, а не серия.
    assert row == (0, 3, "open")


def test_downgrade_is_symmetric_and_keeps_data(tmp_path: Path) -> None:
    db_path = tmp_path / "seeded-down.db"
    db_url = f"sqlite:///{db_path}"

    run_alembic("upgrade", _PREVIOUS, db_url=db_url)
    _seed(db_path)
    run_alembic("upgrade", "head", db_url=db_url)
    run_alembic("downgrade", _PREVIOUS, db_url=db_url)

    assert _counts(db_path) == {"finding": 1, "finding_source_run": 2}
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(finding)")}
    finally:
        conn.close()
    assert "miss_streak" not in columns
