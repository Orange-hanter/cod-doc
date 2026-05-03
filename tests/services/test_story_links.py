"""COD-014 / RFL-072: StoryService.link + list_tasks."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    ModuleStatus,
    Priority,
    StoryLinkKind,
    StoryRelation,
    TaskType,
    UserStoryStatus,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ModuleModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import doc_service as docs
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


def test_link_to_task_validates_target_exists(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        tasks.create(
            session,
            project_id=proj,
            plan_id=plan_id,
            section_id=sec_id,
            task_id="AGN-012",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )
        story = _make_story(session, proj)

        link = stories.link(
            session,
            story_id=story.story_id,
            to_kind=StoryLinkKind.TASK,
            to_ref="AGN-012",
            relation=StoryRelation.IMPLEMENTED_BY,
            author="human:test",
        )
        assert link.row_id is not None
        assert link.to_ref == "AGN-012"

        # Unknown task → broken link error.
        with pytest.raises(stories.BrokenLinkError):
            stories.link(
                session,
                story_id=story.story_id,
                to_kind=StoryLinkKind.TASK,
                to_ref="GHOST-999",
                relation=StoryRelation.IMPLEMENTED_BY,
                author="human:test",
            )


def test_link_to_document_validates_target(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        docs.create(
            session,
            project_id=proj,
            doc_key="modules/M1-auth/overview",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.ACTIVE,
            title="Auth Overview",
            author="human:test",
            owner="human:test",
        )
        story = _make_story(session, proj)

        link = stories.link(
            session,
            story_id=story.story_id,
            to_kind=StoryLinkKind.DOCUMENT,
            to_ref="modules/M1-auth/overview",
            relation=StoryRelation.SPECIFIED_IN,
            author="human:test",
        )
        assert link.to_ref == "modules/M1-auth/overview"


def test_link_to_module_validates_target(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        m = ModuleModel(
            project_id=proj,
            module_id="M1-auth",
            name="Auth",
            status=ModuleStatus.ACTIVE.value,
        )
        session.add(m)
        session.flush()
        story = _make_story(session, proj)
        link = stories.link(
            session,
            story_id=story.story_id,
            to_kind=StoryLinkKind.MODULE,
            to_ref="M1-auth",
            relation=StoryRelation.OWNED_BY,
            author="human:test",
        )
        assert link.to_ref == "M1-auth"


def test_link_dedup_skips_existing_edge(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
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
        story = _make_story(session, proj)

        first = stories.link(
            session,
            story_id=story.story_id,
            to_kind=StoryLinkKind.TASK,
            to_ref="TST-001",
            relation=StoryRelation.IMPLEMENTED_BY,
            author="human:test",
        )
        second = stories.link(
            session,
            story_id=story.story_id,
            to_kind=StoryLinkKind.TASK,
            to_ref="TST-001",
            relation=StoryRelation.IMPLEMENTED_BY,
            author="human:test",
        )
        assert first.row_id == second.row_id  # de-duped, same row returned

        all_links = stories.list_links(session, story.story_id)
        assert len(all_links) == 1
