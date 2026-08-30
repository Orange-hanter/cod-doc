"""COD-014 / RFL-072: StoryService.list_tasks + coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    Priority,
    StoryLinkKind,
    StoryRelation,
    TaskStatus,
    TaskType,
    UserStoryStatus,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import story_service as stories
from cod_doc.services import task_service as tasks

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


def _seed_plan_with_section(session: Session, project_id: int) -> tuple[int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="X", slug="A-X", position=0)
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id


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


def _link_task(session, story_sid: str, task_id: str) -> None:  # type: ignore[no-untyped-def]
    stories.link(
        session,
        story_id=story_sid,
        to_kind=StoryLinkKind.TASK,
        to_ref=task_id,
        relation=StoryRelation.IMPLEMENTED_BY,
        author="human:test",
    )


def test_list_tasks_returns_only_implemented_by(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        for tid in ("TST-001", "TST-002", "TST-003"):
            tasks.create(
                session,
                project_id=proj,
                plan_id=plan_id,
                section_id=sec_id,
                task_id=tid,
                title=tid,
                type=TaskType.FEATURE,
                priority=Priority.LOW,
                author="human:test",
            )
        story = _make_story(session, proj)
        _link_task(session, story.story_id, "TST-001")
        _link_task(session, story.story_id, "TST-002")
        # Add a non-implemented_by edge — should be ignored by list_tasks.
        stories.link(
            session,
            story_id=story.story_id,
            to_kind=StoryLinkKind.TASK,
            to_ref="TST-003",
            relation=StoryRelation.SPECIFIED_IN,
            author="human:test",
        )

        items = stories.list_tasks(session, story.story_id)
        assert {t.task_id for t in items} == {"TST-001", "TST-002"}


def test_coverage_draft_pinned(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj, status=UserStoryStatus.DRAFT)
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.DRAFT


def test_coverage_deferred_pinned(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj, status=UserStoryStatus.DEFERRED)
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.DEFERRED


def test_coverage_accepted_when_no_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj, status=UserStoryStatus.ACCEPTED)
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.ACCEPTED
        assert cov.tasks_total == 0


def test_coverage_in_progress_when_any_task_started(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        for tid in ("TST-001", "TST-002"):
            tasks.create(
                session,
                project_id=proj,
                plan_id=plan_id,
                section_id=sec_id,
                task_id=tid,
                title=tid,
                type=TaskType.FEATURE,
                priority=Priority.LOW,
                author="human:test",
            )
        story = _make_story(session, proj)
        _link_task(session, story.story_id, "TST-001")
        _link_task(session, story.story_id, "TST-002")
        # Move one to in-progress.
        tasks.update_status(
            session,
            task_id="TST-001",
            new_status=TaskStatus.IN_PROGRESS,
            author="human:test",
            via_checkout=True,
        )
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.IN_PROGRESS
        assert cov.tasks_in_progress == 1
        assert cov.tasks_done == 0


def test_coverage_delivered_requires_all_done_and_acceptance_met(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        tasks.create(
            session,
            project_id=proj,
            plan_id=plan_id,
            section_id=sec_id,
            task_id="TST-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.LOW,
            author="human:test",
        )
        story = _make_story(session, proj, acceptance=["A1.", "A2."])
        _link_task(session, story.story_id, "TST-001")

        # Task done, but only 1 of 2 acceptance criteria met — still in-progress.
        tasks.complete(session, task_id="TST-001", author="human:test")
        stories.set_criterion_met(
            session,
            story_id=story.story_id,
            position=0,
            met=True,
            author="human:test",
        )
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.IN_PROGRESS

        # Mark final criterion met → delivered.
        stories.set_criterion_met(
            session,
            story_id=story.story_id,
            position=1,
            met=True,
            author="human:test",
        )
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.DELIVERED
        assert cov.acceptance_total == 2
        assert cov.acceptance_met == 2


def test_coverage_unknown_story_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(stories.StoryNotFoundError):
        stories.coverage(session, "GHOST-001")
