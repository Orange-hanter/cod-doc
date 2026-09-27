"""SQL дополнения обязан пережить миграции.

`prelude.zsh` зашивает имена колонок строками: `user_story.narrative` (а не
`title`), `routine.enabled` (а не `status`), `plan.scope` в роли идентификатора.
Переименование колонки миграцией сломает дополнение молча — ни один другой
гейт этого не видит, потому что zsh никто не типизирует.

Поэтому каждый запрос гоняем по СВЕЖЕЙ схеме (`alembic upgrade head`), а не по
живой БД: так новая миграция подхватывается автоматически.
"""

from __future__ import annotations

import shutil
import sqlite3
from typing import TYPE_CHECKING

import pytest

from cod_doc.cli.completion import PRELUDE_PATH
from cod_doc.cli.completion.sources import COMPLETION_QUERIES, EXTRA_FILTER, PROJECT_FILTER
from cod_doc.cli.completion.zsh import PreludeSlotError, expand_prelude, prelude_slots
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def fresh_schema_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Пустая БД с актуальной схемой: alembic upgrade head в tmp."""
    db_path = tmp_path_factory.mktemp("completion-sql") / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")
    return db_path


def _plain(sql: str, *, slug: str | None = None, extra: str = "") -> str:
    """Развернуть zsh-плейсхолдеры в обычный SQL."""
    project = f"and p.slug = '{slug}'" if slug else ""
    return sql.replace(PROJECT_FILTER, project).replace(EXTRA_FILTER, extra)


@pytest.mark.parametrize("key", sorted(COMPLETION_QUERIES))
def test_query_runs_against_current_schema(key: str, fresh_schema_db: Path) -> None:
    """Запрос обязан выполниться — колонки и таблицы существуют."""
    with sqlite3.connect(f"file:{fresh_schema_db}?mode=ro", uri=True) as conn:
        conn.execute(_plain(COMPLETION_QUERIES[key])).fetchall()


@pytest.mark.parametrize("key", sorted(COMPLETION_QUERIES))
def test_query_runs_with_project_filter(key: str, fresh_schema_db: Path) -> None:
    """Ветка с известным слагом собирается в валидный SQL."""
    with sqlite3.connect(f"file:{fresh_schema_db}?mode=ro", uri=True) as conn:
        conn.execute(_plain(COMPLETION_QUERIES[key], slug="cod-doc")).fetchall()


def test_extra_filter_branches_are_valid_sql(fresh_schema_db: Path) -> None:
    """Сужение по --plan и по doc_key — те же ветки, что строит prelude."""
    with sqlite3.connect(f"file:{fresh_schema_db}?mode=ro", uri=True) as conn:
        conn.execute(
            _plain(
                COMPLETION_QUERIES["plan_sections"],
                slug="cod-doc",
                extra="and pl.scope = 'stabilization-2026-06'",
            )
        ).fetchall()
        conn.execute(
            _plain(
                COMPLETION_QUERIES["doc_sections"],
                slug="cod-doc",
                extra="and d.doc_key = 'docs/system/ARCHITECTURE'",
            )
        ).fetchall()
        conn.execute(
            _plain(
                COMPLETION_QUERIES["task_blocker_candidates"],
                slug="cod-doc",
                extra="and t.task_id = 'TA-001' and d.kind = 'blocks'",
            )
        ).fetchall()


@pytest.fixture
def seeded_deps_db(fresh_schema_db: Path, tmp_path: Path) -> Path:
    """Копия свежей схемы с проектом `p`, задачами TA-001..TA-004 и рёбрами.

    Рёбра (from = задача, to = блокер): TA-001→TA-002 blocks,
    TA-001→TA-003 relates, TA-004→TA-003 blocks.
    """
    db_path = tmp_path / "state.db"
    shutil.copyfile(fresh_schema_db, db_path)
    now = "2026-09-27 00:00:00"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "insert into project (row_id, slug, title, root_path, created, updated)"
            " values (1, 'p', 'P', '/tmp/p', ?, ?)",
            (now, now),
        )
        for row_id in range(1, 5):
            conn.execute(
                "insert into task (row_id, project_id, task_id, plan_id, section_id, title,"
                " status, type, priority, created, last_updated)"
                " values (?, 1, ?, 1, 1, ?, 'todo', 'task', 'p2', ?, ?)",
                (row_id, f"TA-00{row_id}", f"Задача {row_id}", now, now),
            )
        conn.executemany(
            "insert into dependency (from_task_id, to_task_id, kind) values (?, ?, ?)",
            [(1, 2, "blocks"), (1, 3, "relates"), (4, 3, "blocks")],
        )
    return db_path


def test_blocker_candidates_exclude_existing_blockers_of_same_kind(
    seeded_deps_db: Path,
) -> None:
    """add-dep предлагает тех, кто владельца ещё не блокирует ребром этой kind.

    Сам владелец в SQL остаётся — его отсекает zsh-функция.
    """
    sql = COMPLETION_QUERIES["task_blocker_candidates"]
    with sqlite3.connect(f"file:{seeded_deps_db}?mode=ro", uri=True) as conn:
        blocks = {
            row[0].split(":", 1)[0]
            for row in conn.execute(
                _plain(sql, slug="p", extra="and t.task_id = 'TA-001' and d.kind = 'blocks'")
            )
        }
        relates = {
            row[0].split(":", 1)[0]
            for row in conn.execute(
                _plain(sql, slug="p", extra="and t.task_id = 'TA-001' and d.kind = 'relates'")
            )
        }
    assert blocks == {"TA-001", "TA-003", "TA-004"}
    assert relates == {"TA-001", "TA-002", "TA-004"}


@pytest.mark.parametrize("key", sorted(COMPLETION_QUERIES))
def test_query_returns_two_column_describe_rows(key: str, fresh_schema_db: Path) -> None:
    """_describe читает ОДНУ колонку вида `значение:описание`."""
    with sqlite3.connect(f"file:{fresh_schema_db}?mode=ro", uri=True) as conn:
        cursor = conn.execute(_plain(COMPLETION_QUERIES[key]))
        assert len(cursor.description) == 1, f"{key}: дополнение ждёт одну колонку"


def test_every_query_is_project_scoped() -> None:
    """Hub-БД держит много проектов; неотфильтрованный список врёт."""
    for key, sql in COMPLETION_QUERIES.items():
        assert PROJECT_FILTER in sql, f"{key}: нет фильтра по проекту"


def test_queries_and_prelude_slots_match() -> None:
    """Мёртвый запрос — такой же дрейф, как и мёртвый плейсхолдер."""
    slots = prelude_slots(PRELUDE_PATH.read_text(encoding="utf-8"))
    assert slots == set(COMPLETION_QUERIES), (
        f"без плейсхолдера в prelude.zsh: {sorted(set(COMPLETION_QUERIES) - slots)}; "
        f"без запроса в sources.py: {sorted(slots - set(COMPLETION_QUERIES))}"
    )


def test_unknown_slot_is_rejected() -> None:
    with pytest.raises(PreludeSlotError):
        expand_prelude('rows=( $(_cod_doc_sql "@@SQL:no_such_query@@") )')
