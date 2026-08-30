"""PCA-010: heartbeat_service composed snapshot for an iteration start."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, TaskType, UserStory, UserStoryStatus
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.infra.repositories import UserStoryRepository
from cod_doc.services import heartbeat_service, task_service
from cod_doc.services.heartbeat_service import PAYLOAD_BUDGET_BYTES

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_plan(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="hb-proj", title="Heartbeat Proj", root_path="/tmp/hb", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="hb-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Phase 1", slug="A-Phase-1", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _create_task(session, p, pl, s, **kw):  # type: ignore[no-untyped-def]
    return task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        **kw,
    )


# --------------------------------------------------------------------------- #
# Shape                                                                        #
# --------------------------------------------------------------------------- #


def test_heartbeat_returns_full_shape_for_pending_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _create_task(session, p, pl, s, task_id="HB-001", title="t")
        ctx = heartbeat_service.heartbeat_context(session, task_id="HB-001")

    assert set(ctx.keys()) == {
        "task",
        "ancestry",
        "linked_docs_summary",
        "task_documents",
        "pending_approvals",
        "recent_changes",
        "active_skills_hint",
        "next_action_guess",
    }
    assert ctx["task"]["id"] == "HB-001"
    assert ctx["task"]["status"] == "pending"
    assert ctx["task"]["blocked_by"] == []
    assert ctx["ancestry"]["project"]["slug"] == "hb-proj"
    assert ctx["ancestry"]["plan"]["scope"] == "hb-plan"
    assert ctx["ancestry"]["section"]["letter"] == "A"
    assert ctx["ancestry"]["story"] is None
    assert ctx["recent_changes"] == []
    assert ctx["active_skills_hint"] == ["orchestrator"]


def test_heartbeat_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_plan(session)
        with pytest.raises(task_service.TaskNotFoundError):
            heartbeat_service.heartbeat_context(session, task_id="NOPE-001")


# --------------------------------------------------------------------------- #
# Payload size invariant                                                       #
# --------------------------------------------------------------------------- #


def test_heartbeat_payload_under_4kb_for_typical_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """≤ 4 KB JSON-encoded for a representative task with story + a few blockers."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        UserStoryRepository(session).add(
            UserStory(
                project_id=p,
                story_id="US-HB",
                persona="dev",
                narrative="хочу X",
                status=UserStoryStatus.ACCEPTED,
                priority=Priority.HIGH,
            )
        )
        _create_task(session, p, pl, s, task_id="HB-010", title="blocker A")
        _create_task(session, p, pl, s, task_id="HB-020", title="blocker B")
        _create_task(
            session,
            p,
            pl,
            s,
            task_id="HB-030",
            title="task with story and 2 blockers, average title length",
            blocked_by=["HB-010", "HB-020"],
            story_id="US-HB",
        )
        ctx = heartbeat_service.heartbeat_context(session, task_id="HB-030")

    encoded = json.dumps(ctx, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= PAYLOAD_BUDGET_BYTES, f"payload {len(encoded)} > {PAYLOAD_BUDGET_BYTES}"
    assert ctx["task"]["blocked_by"] == ["HB-010", "HB-020"]
    assert ctx["ancestry"]["story"]["id"] == "US-HB"


def test_heartbeat_trims_long_titles(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    long_title = "x" * 500
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _create_task(session, p, pl, s, task_id="HB-040", title=long_title)
        ctx = heartbeat_service.heartbeat_context(session, task_id="HB-040")

    assert len(ctx["task"]["title"]) <= 160
    assert ctx["task"]["title"].endswith("…")


# --------------------------------------------------------------------------- #
# Cursor semantics (since_revision_id)                                         #
# --------------------------------------------------------------------------- #


def test_heartbeat_no_cursor_returns_empty_recent_changes(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _create_task(session, p, pl, s, task_id="HB-001", title="t")
        # Generate additional revisions (status updates).
        from cod_doc.domain.entities import TaskStatus

        task_service.update_status(
            session,
            task_id="HB-001",
            new_status=TaskStatus.IN_PROGRESS,
            author="x",
            via_checkout=True,
        )

        ctx = heartbeat_service.heartbeat_context(session, task_id="HB-001")

    assert ctx["recent_changes"] == []


def test_heartbeat_with_cursor_returns_only_newer_revisions(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """since_revision_id → revisions strictly after that cursor."""
    from cod_doc.domain.entities import EntityKind, TaskStatus
    from cod_doc.services import revision_service as rev

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _create_task(session, p, pl, s, task_id="HB-001", title="t")
        # Initial revision is from create. Now produce 3 more.
        task_service.update_status(
            session,
            task_id="HB-001",
            new_status=TaskStatus.IN_PROGRESS,
            author="x",
            via_checkout=True,
        )
        task_service.update_status(
            session, task_id="HB-001", new_status=TaskStatus.PENDING, author="x"
        )
        task_service.update_status(
            session,
            task_id="HB-001",
            new_status=TaskStatus.IN_PROGRESS,
            author="x",
            via_checkout=True,
        )

        all_revs = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        assert len(all_revs) == 4  # 1 create + 3 status updates
        cursor_rev_id = all_revs[1].revision_id  # after create + first status

        ctx = heartbeat_service.heartbeat_context(
            session, task_id="HB-001", since_revision_id=cursor_rev_id
        )

    # Cursor is exclusive: should return revs after position 1 (i.e. positions 2+3 → 2 entries).
    assert len(ctx["recent_changes"]) == 2
    ops = [c["op"] for c in ctx["recent_changes"]]
    assert all(op == "status" for op in ops)


def test_heartbeat_unknown_cursor_returns_empty_recent_changes(
    engine_with_schema,
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _create_task(session, p, pl, s, task_id="HB-001", title="t")
        ctx = heartbeat_service.heartbeat_context(
            session, task_id="HB-001", since_revision_id="01J0000NONEXISTENT0001234567"
        )

    assert ctx["recent_changes"] == []


def test_heartbeat_cursor_caps_at_20_entries(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """recent_changes capped at _RECENT_CHANGES_MAX (20)."""
    from cod_doc.domain.entities import EntityKind, TaskStatus
    from cod_doc.services import revision_service as rev

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = _create_task(session, p, pl, s, task_id="HB-001", title="t")

        # 25 status flips
        states = [TaskStatus.IN_PROGRESS, TaskStatus.PENDING]
        for i in range(25):
            task_service.update_status(
                session,
                task_id="HB-001",
                new_status=states[i % 2],
                author="x",
                via_checkout=True,
            )

        all_revs = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        cursor = all_revs[0].revision_id  # after the create — see all 25 status flips

        ctx = heartbeat_service.heartbeat_context(
            session, task_id="HB-001", since_revision_id=cursor
        )

    assert len(ctx["recent_changes"]) == 20


# --------------------------------------------------------------------------- #
# next_action_guess                                                            #
# --------------------------------------------------------------------------- #


def test_next_action_guess_pending_no_blockers(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _create_task(session, p, pl, s, task_id="HB-001", title="t")
        ctx = heartbeat_service.heartbeat_context(session, task_id="HB-001")
    assert "checkout" in ctx["next_action_guess"]


def test_next_action_guess_when_blocked_by_open_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _create_task(session, p, pl, s, task_id="HB-010", title="blocker")
        _create_task(session, p, pl, s, task_id="HB-020", title="blocked", blocked_by=["HB-010"])
        ctx = heartbeat_service.heartbeat_context(session, task_id="HB-020")

    assert "wait on blockers" in ctx["next_action_guess"]
    assert "HB-010" in ctx["next_action_guess"]
