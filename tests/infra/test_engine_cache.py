"""STO-026: `db_for_entry` переиспользует движок, а не плодит пулы.

`mcp/tools/_db.py` зовёт `db_for_entry` на каждый тул и выбрасывает движок.
На SQLite это лишние connect+PRAGMA, на Postgres — новый пул соединений
на каждый вызов.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.config import ProjectEntry
from cod_doc.infra.db import (
    cached_engine,
    db_for_entry,
    dispose_cached_engines,
    make_engine,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_db_for_entry_reuses_engine(tmp_path: Path) -> None:
    entry = ProjectEntry(name="demo", path=str(tmp_path))

    _first_factory, first = db_for_entry(entry)
    _second_factory, second = db_for_entry(entry)

    assert first is second


def test_different_projects_get_different_engines(tmp_path: Path) -> None:
    one = ProjectEntry(name="one", path=str(tmp_path / "one"))
    two = ProjectEntry(name="two", path=str(tmp_path / "two"))

    _f1, first = db_for_entry(one)
    _f2, second = db_for_entry(two)

    assert first is not second


def test_dispose_cached_engines_drops_the_cache(tmp_path: Path) -> None:
    entry = ProjectEntry(name="demo", path=str(tmp_path))
    _factory, first = db_for_entry(entry)

    dispose_cached_engines()

    _factory2, second = db_for_entry(entry)
    assert first is not second


def test_in_memory_urls_are_not_shared() -> None:
    """Для `:memory:` новый движок — это новая пустая БД; делить её нельзя."""
    assert cached_engine("sqlite://") is not cached_engine("sqlite://")


def test_cached_engine_is_a_real_engine(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'x.db'}"
    engine = cached_engine(url)

    assert engine is cached_engine(url)
    assert engine is not make_engine(url), "make_engine остаётся не кэширующим"
