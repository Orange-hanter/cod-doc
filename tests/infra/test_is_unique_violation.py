"""``is_unique_violation``: «уже существует» — только для своего ограничения.

Подстрока по имени колонки совпадала с любым сообщением о ней (NOT NULL,
CHECK, внешний ключ), и чужой сбой молча превращался в «уже существует».
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.sql_helpers import is_unique_violation

_KW = {
    "constraint": "uq_user_story_project_story_id",
    "table": "user_story",
    "columns": ("project_id", "story_id"),
}


def _sqlite(msg: str) -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception(msg))


class _PgError(Exception):
    def __init__(self, constraint_name: str | None) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self.diag = SimpleNamespace(constraint_name=constraint_name)


def test_sqlite_exact_unique_message_matches() -> None:
    exc = _sqlite("UNIQUE constraint failed: user_story.project_id, user_story.story_id")
    assert is_unique_violation(exc, **_KW)


@pytest.mark.parametrize(
    "msg",
    [
        "NOT NULL constraint failed: user_story.story_id",
        "CHECK constraint failed: user_story.story_id",
        "UNIQUE constraint failed: user_story.story_id",
        "FOREIGN KEY constraint failed",
    ],
)
def test_sqlite_other_failures_do_not_match(msg: str) -> None:
    assert not is_unique_violation(_sqlite(msg), **_KW)


def test_postgres_matches_by_constraint_name_only() -> None:
    assert is_unique_violation(IntegrityError("INSERT", {}, _PgError(_KW["constraint"])), **_KW)
    assert not is_unique_violation(
        IntegrityError("INSERT", {}, _PgError("user_story_section_id_fkey")), **_KW
    )


def _real_sqlite_error(ddl: str, insert: str) -> IntegrityError:
    """Настоящий ``sqlite3.IntegrityError`` с расширенным кодом ошибки."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(ddl)
        conn.execute(insert)
        try:
            conn.execute(insert)
        except sqlite3.IntegrityError as err:
            return IntegrityError("INSERT", {}, err)
    finally:
        conn.close()
    raise AssertionError("insert did not fail")


def test_real_sqlite_unique_violation_matches() -> None:
    exc = _real_sqlite_error(
        "CREATE TABLE user_story (project_id INT, story_id TEXT, UNIQUE (project_id, story_id))",
        "INSERT INTO user_story VALUES (1, 'S-1')",
    )
    assert getattr(exc.orig, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
    assert is_unique_violation(exc, **_KW)


def test_real_sqlite_unique_on_other_columns_does_not_match() -> None:
    exc = _real_sqlite_error(
        "CREATE TABLE user_story (project_id INT, story_id TEXT UNIQUE)",
        "INSERT INTO user_story VALUES (1, 'S-1')",
    )
    assert not is_unique_violation(exc, **_KW)


def test_sqlite_errorname_other_than_unique_does_not_match() -> None:
    """Текст похож на UNIQUE, а расширенный код говорит иное — верим коду."""
    err = sqlite3.IntegrityError(
        "UNIQUE constraint failed: user_story.project_id, user_story.story_id"
    )
    err.sqlite_errorname = "SQLITE_CONSTRAINT_PRIMARYKEY"
    assert not is_unique_violation(IntegrityError("INSERT", {}, err), **_KW)


def test_sqlite_message_whitespace_is_tolerated() -> None:
    exc = _sqlite("UNIQUE constraint failed:  user_story.project_id ,user_story.story_id \n")
    assert is_unique_violation(exc, **_KW)
