"""``is_unique_violation``: «уже существует» — только для своего ограничения.

Подстрока по имени колонки совпадала с любым сообщением о ней (NOT NULL,
CHECK, внешний ключ), и чужой сбой молча превращался в «уже существует».
"""

from __future__ import annotations

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
