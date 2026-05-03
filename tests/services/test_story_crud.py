"""COD-014 / RFL-072: StoryService — create / list / update_status / acceptance."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import EntityKind, Priority, UserStoryStatus
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import revision_service as rev
from cod_doc.services import story_service as stories

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _make_story(
    session: Session,
    project_id: int,
    *,
    story_id: str = "US-001",
    status: UserStoryStatus = UserStoryStatus.ACCEPTED,
    acceptance: list[str] | None = None,
):
    return stories.create(
        session,
        project_id=project_id,
        story_id=story_id,
        persona="Agency Owner",
        narrative="As X, I want Y, so Z.",
        priority=Priority.MEDIUM,
        status=status,
        author="human:test",
        acceptance=acceptance,
    )


# ============================================================================ #
# create / get / list_for_project                                              #
# ============================================================================ #


def test_create_persists_story_and_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj)

        assert story.row_id is not None
        assert story.story_id == "US-001"
        assert story.status is UserStoryStatus.ACCEPTED

        history = rev.list_for_entity(session, EntityKind.STORY, story.row_id)
        assert len(history) == 1
        payload = json.loads(history[0].diff)
        assert payload["op"] == "create"


def test_create_with_initial_acceptance(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(
            session,
            proj,
            acceptance=["Invite link expires in 7 days.", "Revoked invite cannot be used."],
        )
        items = stories.list_acceptance(session, story.story_id)
        assert [a.criterion for a in items] == [
            "Invite link expires in 7 days.",
            "Revoked invite cannot be used.",
        ]
        assert all(a.met is False for a in items)
        assert [a.position for a in items] == [0, 1]


def test_create_duplicate_story_id_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        _make_story(session, proj, story_id="US-001")
        with pytest.raises(stories.StoryAlreadyExistsError):
            _make_story(session, proj, story_id="US-001")


def test_create_rejects_invalid_story_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.services import validation as v

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        with pytest.raises(v.ValidationError) as exc:
            _make_story(session, proj, story_id="us-001")  # lowercase
        assert exc.value.code == "US-001"


def test_get_returns_none_for_unknown(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        assert stories.get(session, "GHOST-001") is None


def test_list_for_project_orders_by_story_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        for sid in ("US-003", "US-001", "US-002"):
            _make_story(session, proj, story_id=sid)
        items = stories.list_for_project(session, proj)
        assert [s.story_id for s in items] == ["US-001", "US-002", "US-003"]


# ============================================================================ #
# update_status                                                                #
# ============================================================================ #


def test_update_status_writes_revision_and_chains(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj)

        updated = stories.update_status(
            session,
            story_id=story.story_id,
            new_status=UserStoryStatus.DELIVERED,
            author="human:test",
        )
        assert updated.status is UserStoryStatus.DELIVERED

        history = rev.list_for_entity(session, EntityKind.STORY, story.row_id)
        assert len(history) == 2
        diff = json.loads(history[1].diff)
        assert diff["op"] == "status"
        assert diff["new"] == "delivered"
        assert history[1].parent_revision_id == history[0].revision_id


def test_update_status_no_op_writes_no_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj)
        stories.update_status(
            session,
            story_id=story.story_id,
            new_status=UserStoryStatus.ACCEPTED,
            author="human:test",
        )
        assert len(rev.list_for_entity(session, EntityKind.STORY, story.row_id)) == 1


def test_update_status_concurrency_conflict(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj)
        head = rev.list_for_entity(session, EntityKind.STORY, story.row_id)[0].revision_id

        stories.update_status(
            session,
            story_id=story.story_id,
            new_status=UserStoryStatus.DELIVERED,
            author="other",
        )

        with pytest.raises(rev.RevisionConflictError):
            stories.update_status(
                session,
                story_id=story.story_id,
                new_status=UserStoryStatus.DEFERRED,
                author="human:test",
                expected_parent_revision_id=head,
            )


# ============================================================================ #
# acceptance criteria                                                          #
# ============================================================================ #


def test_add_criterion_appends_at_next_position(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj, acceptance=["First."])

        a2 = stories.add_criterion(
            session,
            story_id=story.story_id,
            criterion="Second.",
            author="human:test",
        )
        assert a2.position == 1
        a3 = stories.add_criterion(
            session,
            story_id=story.story_id,
            criterion="Third.",
            author="human:test",
        )
        assert a3.position == 2


def test_set_criterion_met_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj, acceptance=["AC1.", "AC2."])

        updated = stories.set_criterion_met(
            session,
            story_id=story.story_id,
            position=0,
            met=True,
            author="human:test",
        )
        assert updated.met is True

        items = stories.list_acceptance(session, story.story_id)
        assert items[0].met is True and items[1].met is False

        history = rev.list_for_entity(session, EntityKind.STORY, story.row_id)
        assert any(json.loads(r.diff)["op"] == "criterion_met" for r in history)


def test_set_criterion_met_unknown_position_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj, acceptance=["Only."])
        with pytest.raises(stories.AcceptanceNotFoundError):
            stories.set_criterion_met(
                session,
                story_id=story.story_id,
                position=99,
                met=True,
                author="human:test",
            )
