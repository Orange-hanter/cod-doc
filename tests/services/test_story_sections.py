"""ADO-143: реестр секций историй + привязка истории к секции."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, UserStoryStatus
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import revision_service as rev
from cod_doc.services import story_service as stories
from cod_doc.services.validation import ValidationError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return int(proj.row_id)


def _make_story(session: Session, project_id: int, story_id: str = "US-001", **kw):  # type: ignore[no-untyped-def]
    return stories.create(
        session,
        project_id=project_id,
        story_id=story_id,
        persona="Управляющий",
        narrative="Как управляющий, я хочу X, чтобы Y.",
        priority=Priority.MEDIUM,
        status=UserStoryStatus.ACCEPTED,
        author="human:test",
        **kw,
    )


# ============================================================================ #
# create_section                                                               #
# ============================================================================ #


def test_create_section_assigns_incremental_position(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        a = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        b = stories.create_section(
            session, project_id=pid, key="annex", title="Аннекс", author="human:test"
        )
        assert (a.position, b.position) == (1, 2)
        assert [s.key for s in stories.list_sections(session, pid)] == ["module-1", "annex"]


def test_create_section_rejects_unsafe_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ключ уходит в путь роута и в CSS-селектор — слэши, точки и пробелы под запретом."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        for bad in ("Module 1", "module/1", "module.1", "Модуль-1", "MODULE-1", ""):
            with pytest.raises(ValidationError):
                stories.create_section(
                    session, project_id=pid, key=bad, title="X", author="human:test"
                )


def test_section_revision_does_not_land_in_story_history(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Адрес ревизии — пара (entity_kind, entity_id).

    У `story_section` и `user_story` независимая нумерация row_id, поэтому
    первая секция и первая история обе получают row_id=1. Пока секция писала
    ревизию под `EntityKind.STORY`, её `create_section` оказывался в истории
    чужой истории.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        sec = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        story = _make_story(session, pid)
        # Предпосылка теста: без совпадения row_id коллизию не поймать.
        assert sec.row_id == story.row_id
        story_row = story.row_id
        section_row = sec.row_id

    with transactional(factory) as session:
        story_hist = rev.list_for_entity(session, EntityKind.STORY, story_row)
        ops = [json.loads(r.diff)["op"] for r in story_hist]
        assert "create_section" not in ops, "ревизия секции просочилась в историю истории"

        section_hist = rev.list_for_entity(session, EntityKind.STORY_SECTION, section_row)
        assert [json.loads(r.diff)["op"] for r in section_hist] == ["create_section"]


def test_create_section_rejects_duplicate_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        with pytest.raises(stories.SectionAlreadyExistsError):
            stories.create_section(
                session, project_id=pid, key="module-1", title="Другое", author="human:test"
            )


# ============================================================================ #
# assign_section                                                               #
# ============================================================================ #


def test_assign_section_writes_revision_and_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        story = _make_story(session, pid)
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        row_id = story.row_id

    with transactional(factory) as session:
        assert stories.get(session, "US-001").section_id is not None
        history = rev.list_for_entity(session, EntityKind.STORY, row_id)
        ops = [json.loads(r.diff)["op"] for r in history]
        assert "section" in ops
        latest = next(json.loads(r.diff) for r in history if json.loads(r.diff)["op"] == "section")
        assert latest["old"] is None
        assert latest["new"] == "module-1"

        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "story.section_changed")
        ).scalar_one()
        assert ev.scope_id == "US-001"


def test_assign_same_section_twice_is_noop(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Идемпотентность как у update_status: повтор не плодит ревизии."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        story = _make_story(session, pid)
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        row_id = story.row_id

    with transactional(factory) as session:
        history = rev.list_for_entity(session, EntityKind.STORY, row_id)
        section_revs = [r for r in history if json.loads(r.diff)["op"] == "section"]
        assert len(section_revs) == 1


def test_assign_section_none_detaches(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        _make_story(session, pid)
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        result = stories.assign_section(session, story_id="US-001", key=None, author="human:test")
        assert result is None

    with transactional(factory) as session:
        assert stories.get(session, "US-001").section_id is None


def test_assign_unknown_section_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        _make_story(session, pid)
        with pytest.raises(stories.SectionNotFoundError):
            stories.assign_section(session, story_id="US-001", key="nope", author="human:test")


def test_assign_section_unknown_story_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        with pytest.raises(stories.StoryNotFoundError):
            stories.assign_section(session, story_id="US-999", key="module-1", author="human:test")


def test_sections_are_scoped_per_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        p1 = _seed_project(session)
        other = ProjectModel(slug="q", title="Q", root_path="/tmp/q", config_json={})
        other.created = now
        other.updated = now
        session.add(other)
        session.flush()
        p2 = int(other.row_id)

        stories.create_section(
            session, project_id=p1, key="module-1", title="Первый", author="human:test"
        )
        stories.create_section(
            session, project_id=p2, key="module-1", title="Второй", author="human:test"
        )
        assert [s.title for s in stories.list_sections(session, p1)] == ["Первый"]
        assert [s.title for s in stories.list_sections(session, p2)] == ["Второй"]


def test_create_story_with_section_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        sec = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        _make_story(session, pid, section_id=sec.row_id)

    with transactional(factory) as session:
        assert stories.get(session, "US-001").section_id == sec.row_id
