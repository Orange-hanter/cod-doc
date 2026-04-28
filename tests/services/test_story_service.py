"""COD-014: StoryService — CRUD / link / coverage."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    ModuleStatus,
    Priority,
    StoryLinkKind,
    StoryRelation,
    TaskStatus,
    TaskType,
    UserStoryStatus,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ModuleModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev
from cod_doc.services import story_service as stories
from cod_doc.services import task_service as tasks

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'stories.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now; proj.updated = now
    session.add(proj); session.flush()
    return proj.row_id


def _seed_plan_with_section(session: Session, project_id: int) -> tuple[int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan); session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="X", slug="A-X", position=0
    )
    session.add(sec); session.flush()
    return plan.row_id, sec.row_id


def _make_story(
    session: Session, project_id: int, *, story_id: str = "US-001",
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
            session, proj,
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
            session, story_id=story.story_id,
            new_status=UserStoryStatus.DELIVERED, author="human:test",
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
            session, story_id=story.story_id,
            new_status=UserStoryStatus.ACCEPTED, author="human:test",
        )
        assert len(rev.list_for_entity(session, EntityKind.STORY, story.row_id)) == 1


def test_update_status_concurrency_conflict(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj)
        head = rev.list_for_entity(session, EntityKind.STORY, story.row_id)[0].revision_id

        stories.update_status(
            session, story_id=story.story_id,
            new_status=UserStoryStatus.DELIVERED, author="other",
        )

        with pytest.raises(rev.RevisionConflictError):
            stories.update_status(
                session, story_id=story.story_id,
                new_status=UserStoryStatus.DEFERRED, author="human:test",
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
            session, story_id=story.story_id, criterion="Second.", author="human:test",
        )
        assert a2.position == 1
        a3 = stories.add_criterion(
            session, story_id=story.story_id, criterion="Third.", author="human:test",
        )
        assert a3.position == 2


def test_set_criterion_met_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        story = _make_story(session, proj, acceptance=["AC1.", "AC2."])

        updated = stories.set_criterion_met(
            session, story_id=story.story_id, position=0, met=True, author="human:test",
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
                session, story_id=story.story_id, position=99,
                met=True, author="human:test",
            )


# ============================================================================ #
# link                                                                          #
# ============================================================================ #


def test_link_to_task_validates_target_exists(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        tasks.create(
            session, project_id=proj, plan_id=plan_id, section_id=sec_id,
            task_id="AGN-012", title="t", type=TaskType.FEATURE,
            priority=Priority.MEDIUM, author="human:test",
        )
        story = _make_story(session, proj)

        link = stories.link(
            session, story_id=story.story_id,
            to_kind=StoryLinkKind.TASK, to_ref="AGN-012",
            relation=StoryRelation.IMPLEMENTED_BY, author="human:test",
        )
        assert link.row_id is not None
        assert link.to_ref == "AGN-012"

        # Unknown task → broken link error.
        with pytest.raises(stories.BrokenLinkError):
            stories.link(
                session, story_id=story.story_id,
                to_kind=StoryLinkKind.TASK, to_ref="GHOST-999",
                relation=StoryRelation.IMPLEMENTED_BY, author="human:test",
            )


def test_link_to_document_validates_target(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        docs.create(
            session, project_id=proj, doc_key="modules/M1-auth/overview",
            type=DocumentType.MODULE_SPEC, status=DocumentStatus.ACTIVE,
            title="Auth Overview", author="human:test", owner="human:test",
        )
        story = _make_story(session, proj)

        link = stories.link(
            session, story_id=story.story_id,
            to_kind=StoryLinkKind.DOCUMENT, to_ref="modules/M1-auth/overview",
            relation=StoryRelation.SPECIFIED_IN, author="human:test",
        )
        assert link.to_ref == "modules/M1-auth/overview"


def test_link_to_module_validates_target(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        m = ModuleModel(
            project_id=proj, module_id="M1-auth", name="Auth",
            status=ModuleStatus.ACTIVE.value,
        )
        session.add(m); session.flush()
        story = _make_story(session, proj)
        link = stories.link(
            session, story_id=story.story_id,
            to_kind=StoryLinkKind.MODULE, to_ref="M1-auth",
            relation=StoryRelation.OWNED_BY, author="human:test",
        )
        assert link.to_ref == "M1-auth"


def test_link_dedup_skips_existing_edge(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        tasks.create(
            session, project_id=proj, plan_id=plan_id, section_id=sec_id,
            task_id="TST-001", title="t", type=TaskType.FEATURE,
            priority=Priority.LOW, author="human:test",
        )
        story = _make_story(session, proj)

        first = stories.link(
            session, story_id=story.story_id,
            to_kind=StoryLinkKind.TASK, to_ref="TST-001",
            relation=StoryRelation.IMPLEMENTED_BY, author="human:test",
        )
        second = stories.link(
            session, story_id=story.story_id,
            to_kind=StoryLinkKind.TASK, to_ref="TST-001",
            relation=StoryRelation.IMPLEMENTED_BY, author="human:test",
        )
        assert first.row_id == second.row_id  # de-duped, same row returned

        all_links = stories.list_links(session, story.story_id)
        assert len(all_links) == 1


# ============================================================================ #
# list_tasks / coverage                                                         #
# ============================================================================ #


def _link_task(session, story_sid: str, task_id: str) -> None:  # type: ignore[no-untyped-def]
    stories.link(
        session, story_id=story_sid,
        to_kind=StoryLinkKind.TASK, to_ref=task_id,
        relation=StoryRelation.IMPLEMENTED_BY, author="human:test",
    )


def test_list_tasks_returns_only_implemented_by(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed_project(session)
        plan_id, sec_id = _seed_plan_with_section(session, proj)
        for tid in ("TST-001", "TST-002", "TST-003"):
            tasks.create(
                session, project_id=proj, plan_id=plan_id, section_id=sec_id,
                task_id=tid, title=tid, type=TaskType.FEATURE,
                priority=Priority.LOW, author="human:test",
            )
        story = _make_story(session, proj)
        _link_task(session, story.story_id, "TST-001")
        _link_task(session, story.story_id, "TST-002")
        # Add a non-implemented_by edge — should be ignored by list_tasks.
        stories.link(
            session, story_id=story.story_id,
            to_kind=StoryLinkKind.TASK, to_ref="TST-003",
            relation=StoryRelation.SPECIFIED_IN, author="human:test",
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
                session, project_id=proj, plan_id=plan_id, section_id=sec_id,
                task_id=tid, title=tid, type=TaskType.FEATURE,
                priority=Priority.LOW, author="human:test",
            )
        story = _make_story(session, proj)
        _link_task(session, story.story_id, "TST-001")
        _link_task(session, story.story_id, "TST-002")
        # Move one to in-progress.
        tasks.update_status(
            session, task_id="TST-001",
            new_status=TaskStatus.IN_PROGRESS, author="human:test",
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
            session, project_id=proj, plan_id=plan_id, section_id=sec_id,
            task_id="TST-001", title="t", type=TaskType.FEATURE,
            priority=Priority.LOW, author="human:test",
        )
        story = _make_story(session, proj, acceptance=["A1.", "A2."])
        _link_task(session, story.story_id, "TST-001")

        # Task done, but only 1 of 2 acceptance criteria met — still in-progress.
        tasks.complete(session, task_id="TST-001", author="human:test")
        stories.set_criterion_met(
            session, story_id=story.story_id, position=0,
            met=True, author="human:test",
        )
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.IN_PROGRESS

        # Mark final criterion met → delivered.
        stories.set_criterion_met(
            session, story_id=story.story_id, position=1,
            met=True, author="human:test",
        )
        cov = stories.coverage(session, story.story_id)
        assert cov.status is stories.CoverageStatus.DELIVERED
        assert cov.acceptance_total == 2
        assert cov.acceptance_met == 2


def test_coverage_unknown_story_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(stories.StoryNotFoundError):
        stories.coverage(session, "GHOST-001")
