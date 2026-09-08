"""ADO-143 smoke: схема ``story_section`` + ``user_story.section_id``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ProjectModel,
    StorySectionModel,
    UserStoryModel,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'sections.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _add_project(session, slug: str = "p") -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return int(proj.row_id)


def _add_story(session, project_id: int, story_id: str, section_id: int | None = None) -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    story = UserStoryModel(
        project_id=project_id,
        story_id=story_id,
        persona="Управляющий",
        narrative="Как управляющий, я хочу X, чтобы Y",
        status="accepted",
        priority="high",
        created=now,
        last_updated=now,
        section_id=section_id,
    )
    session.add(story)
    session.flush()
    return int(story.row_id)


def test_migration_creates_story_section(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    with engine_with_schema.connect() as conn:
        names = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info('user_story')"))}
    assert "story_section" in names
    assert "section_id" in cols


def test_section_key_unique_per_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        p1 = _add_project(session, "one")
        p2 = _add_project(session, "two")
        session.add(StorySectionModel(project_id=p1, key="module-1", title="Запасы", position=1))
        session.flush()
        # Тот же ключ в другом проекте — законен.
        session.add(StorySectionModel(project_id=p2, key="module-1", title="Другое", position=1))
        session.flush()

    with pytest.raises(IntegrityError), transactional(factory) as session:
        proj = session.execute(select(ProjectModel).where(ProjectModel.slug == "one")).scalar_one()
        session.add(
            StorySectionModel(project_id=proj.row_id, key="module-1", title="Дубль", position=2)
        )
        session.flush()


def test_deleting_section_orphans_story_but_keeps_it(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ON DELETE SET NULL: история переживает удаление своей секции."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        # SQLite не проверяет FK без включённого pragma; make_engine его ставит.
        proj_id = _add_project(session, "demo")
        section = StorySectionModel(project_id=proj_id, key="module-1", title="Запасы", position=1)
        session.add(section)
        session.flush()
        _add_story(session, proj_id, "US-001", section_id=section.row_id)
        section_row_id = section.row_id

    with transactional(factory) as session:
        row = session.get(StorySectionModel, section_row_id)
        assert row is not None
        session.delete(row)

    with transactional(factory) as session:
        story = session.execute(
            select(UserStoryModel).where(UserStoryModel.story_id == "US-001")
        ).scalar_one()
        assert story.section_id is None


def test_story_without_section_is_valid(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session, "demo")
        _add_story(session, proj_id, "US-002")

    with transactional(factory) as session:
        story = session.execute(
            select(UserStoryModel).where(UserStoryModel.story_id == "US-002")
        ).scalar_one()
        assert story.section_id is None
