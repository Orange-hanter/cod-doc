"""SQL дополнения обязан пережить миграции.

`prelude.zsh` зашивает имена колонок строками: `user_story.narrative` (а не
`title`), `routine.enabled` (а не `status`), `plan.scope` в роли идентификатора.
Переименование колонки миграцией сломает дополнение молча — ни один другой
гейт этого не видит, потому что zsh никто не типизирует.

Поэтому каждый запрос гоняем по СВЕЖЕЙ схеме (`alembic upgrade head`), а не по
живой БД: так новая миграция подхватывается автоматически.
"""

from __future__ import annotations

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
