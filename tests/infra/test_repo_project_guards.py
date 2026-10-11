"""STO-023: поиск по проектному ключу отвергает невалидные аргументы явно.

``project_id=None`` дал бы ``WHERE project_id IS NULL``, ``0``/``-1`` —
``WHERE project_id = 0``, ``story_id=None`` — ``WHERE story_id IS NULL``: во всех
случаях тихое «не найдено», неотличимое от настоящего промаха.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.db import make_engine, transactional
from cod_doc.infra.repositories import PlanRepository, UserStoryRepository
from tests._alembic import run_alembic
from tests._project_scoped_seed import SCOPE, STORY
from tests._project_scoped_seed import seed_two_projects as _seed

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine


@pytest.fixture
def engine_with_schema(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'guards.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("bad", [None, "1", True])
def test_scoped_repo_lookups_reject_non_int_project_id(
    engine_with_schema: Engine, bad: object
) -> None:
    factory, _ = _seed(engine_with_schema)
    with transactional(factory) as session:
        with pytest.raises(TypeError, match="project_id must be int"):
            PlanRepository(session).get_by_scope(SCOPE, project_id=bad)
        with pytest.raises(TypeError, match="project_id must be int"):
            UserStoryRepository(session).get_by_story_id(STORY, project_id=bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_scoped_repo_lookups_reject_non_positive_project_id(
    engine_with_schema: Engine, bad: int
) -> None:
    factory, _ = _seed(engine_with_schema)
    with transactional(factory) as session:
        with pytest.raises(ValueError, match="project_id must be positive"):
            PlanRepository(session).get_by_scope(SCOPE, project_id=bad)
        with pytest.raises(ValueError, match="project_id must be positive"):
            UserStoryRepository(session).get_by_story_id(STORY, project_id=bad)


@pytest.mark.parametrize("bad", [None, "", 1])
def test_scoped_repo_lookups_reject_empty_or_non_str_key(
    engine_with_schema: Engine, bad: object
) -> None:
    factory, pids = _seed(engine_with_schema)
    pid = next(iter(pids.values()))
    with transactional(factory) as session:
        with pytest.raises((TypeError, ValueError), match="scope must be a non-empty str"):
            PlanRepository(session).get_by_scope(bad, project_id=pid)
        with pytest.raises((TypeError, ValueError), match="story_id must be a non-empty str"):
            UserStoryRepository(session).get_by_story_id(bad, project_id=pid)
