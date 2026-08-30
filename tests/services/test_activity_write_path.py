"""PCA-912: write-path activity-event coverage + error visibility.

Smoke-tests every service touched by ADO-040 to ensure that write operations
emit an activity event in the same transaction. Also verifies that emit errors
are not silently swallowed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import (
    DocumentStatus,
    DocumentType,
    EntityKind,
    Priority,
    StoryLinkKind,
    StoryRelation,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import (
    activity_service,
    adr_service,
    approval_service,
    checkout_service,
    comment_service,
    commit_link_service,
    doc_service,
    repo_index_service,
    task_doc_service,
    task_service,
)
from cod_doc.services.link_service import resolver as link_resolver
from cod_doc.services.story_service import crud as story_crud
from cod_doc.services.story_service import links as story_links

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str = "awp") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _seed_plan(session: Session) -> tuple[int, int, int]:
    proj_id = _seed_project(session)
    plan = PlanModel(
        project_id=proj_id,
        scope="awp-plan",
        created=datetime.now(UTC),
        last_updated=datetime.now(UTC),
    )
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Sec", slug="A-Sec", position=0)
    session.add(sec)
    session.flush()
    return proj_id, plan.row_id, sec.row_id


def _make_task(session: Session, proj_id: int, plan_id: int, sec_id: int, task_id: str) -> int:
    t = task_service.create(
        session,
        project_id=proj_id,
        plan_id=plan_id,
        section_id=sec_id,
        task_id=task_id,
        title=f"Task {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    assert t.task_id is not None
    return t.row_id


# --------------------------------------------------------------------------- #
# Helper semantics
# --------------------------------------------------------------------------- #


def test_actor_kind_for_author_derivation() -> None:
    assert activity_service._actor_kind_for_author("agent:run-X") == "agent"
    assert activity_service._actor_kind_for_author("agent-X") == "agent"
    assert activity_service._actor_kind_for_author("orchestrator-run-X") == "orchestrator"
    assert activity_service._actor_kind_for_author("human:dakh") == "human"
    assert activity_service._actor_kind_for_author("cli") == "human"
    assert activity_service._actor_kind_for_author("mcp") == "system"


def test_emit_for_write_derives_actor_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        ev = activity_service.emit_for_write(session, proj_id, "k", "agent:run-X", summary="x")
        assert ev.actor_kind == "agent"
        assert ev.actor_id == "agent:run-X"


def test_write_revision_and_emit_event_writes_both(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        revision, event = activity_service.write_revision_and_emit_event(
            session,
            project_id=proj_id,
            entity_kind=EntityKind.DOCUMENT,
            entity_id=proj_id,
            author="human:test",
            diff='{"op": "test"}',
            reason="test",
            activity_kind="doc.tested",
            activity_scope_kind="doc",
            activity_scope_id="TEST",
            activity_summary="test event",
        )
        assert revision.revision_id is not None
        assert event.kind == "doc.tested"
        assert event.scope_id == "TEST"


def test_emit_error_is_not_swallowed_in_task_update_status(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """An exception from activity_service.emit must bubble up (PCA-912)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="TSK-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )

    with (
        transactional(factory) as session,
        patch.object(activity_service, "emit", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        task_service.update_status(
            session,
            task_id=task.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:test",
            via_checkout=True,
        )


# --------------------------------------------------------------------------- #
# Task service
# --------------------------------------------------------------------------- #


def test_task_create_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="TSK-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "task.created")
        ).scalar_one()
        assert ev.scope_id == "TSK-001"


def test_task_update_status_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="TSK-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )
        task_service.update_status(
            session,
            task_id=task.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:test",
            via_checkout=True,
        )

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "task.status_changed")
        ).scalar_one()
        assert ev.payload["new_status"] in ("in_progress", "in-progress")


def test_task_complete_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="TSK-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )
        checkout_service.checkout(session, task.task_id, agent="agent-X")
        task_service.complete(session, task_id=task.task_id, author="human:test")

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "task.completed")
        ).scalar_one()
        assert ev.scope_id == "TSK-001"


# --------------------------------------------------------------------------- #
# Document service
# --------------------------------------------------------------------------- #


def _new_doc(session: Session, project_id: int, doc_key: str) -> int:
    d = doc_service.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="Title",
        author="human:test",
        owner="human:test",
    )
    assert d.row_id is not None
    return d.row_id


def test_doc_create_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        _new_doc(session, p, "doc-1")

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.created")
        ).scalar_one()
        assert ev.scope_id == "doc-1"


def test_doc_patch_section_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _new_doc(session, p, "doc-1")
        doc_service.add_section(
            session,
            document_id=doc_id,
            anchor="intro",
            heading="Intro",
            level=1,
            position=0,
            body="hello",
            author="human:test",
        )
        doc_service.patch_section(
            session,
            document_id=doc_id,
            anchor="intro",
            new_body="hello world",
            author="human:test",
        )

    with transactional(factory) as session:
        events = list(
            session.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "doc.section_updated")
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].scope_id == "doc-1#intro"


def test_doc_delete_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        _new_doc(session, p, "doc-del")

    with transactional(factory) as session:
        doc_service.delete(session, project_id=p, doc_key="doc-del", author="cli")

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.deleted")
        ).scalar_one()
        assert ev.scope_id == "doc-del"


# --------------------------------------------------------------------------- #
# ADR / approval / task-doc / story / comment / checkout / commit / link / repo
# --------------------------------------------------------------------------- #


def test_adr_create_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        adr_service.create(session, project_id=p, title="X", author="human:test")

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "adr.created")
        ).scalar_one()
        assert ev.scope_id == "ADR-001"


def test_approval_request_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        approval_service.request(
            session,
            p,
            approval_type="manual",
            requested_by="orchestrator-run-X",
        )

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "approval.requested")
        ).scalar_one()
        assert ev.actor_kind == "orchestrator"


def test_task_doc_put_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task_row = _make_task(session, p, pl, s, "TD-001")
        task_doc_service.put(
            session,
            project_id=p,
            task_row_id=task_row,
            key="plan",
            title="Plan",
            body="step 1",
            author="human:test",
        )

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "task_doc.created")
        ).scalar_one()
        assert ev.scope_id == f"{task_row}:plan"


def test_story_create_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        story_crud.create(
            session,
            project_id=p,
            story_id="US-001",
            persona="X",
            narrative="As X, I want Y.",
            priority=Priority.MEDIUM,
            author="human:test",
        )

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "story.created")
        ).scalar_one()
        assert ev.scope_id == "US-001"


def test_story_link_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        _make_task(session, p, pl, s, "ST-001")
        story_crud.create(
            session,
            project_id=p,
            story_id="US-001",
            persona="X",
            narrative="As X, I want Y.",
            priority=Priority.MEDIUM,
            author="human:test",
        )
        story_links.link(
            session,
            story_id="US-001",
            to_kind=StoryLinkKind.TASK,
            to_ref="ST-001",
            relation=StoryRelation.IMPLEMENTED_BY,
            author="human:test",
        )

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "story.linked")
        ).scalar_one()
        assert ev.payload["to_ref"] == "ST-001"


def test_comment_create_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _new_doc(session, p, "doc-1")
        comment_service.create(
            session,
            document_id=doc_id,
            body="Please fix this.",
            author="human:test",
        )

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "comment.created")
        ).scalar_one()
        assert ev.actor_kind == "human"


def test_checkout_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task = task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="CO-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )
        checkout_service.checkout(session, task.task_id, agent="agent-X")

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "task.checked_out")
        ).scalar_one()
        assert ev.actor_kind == "agent"


def test_commit_link_import_emits_event(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "demo-repo"
    repo.mkdir()

    def _git(*args: str) -> None:
        __import__("subprocess").run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        )

    _git("init", "-q", "--initial-branch=main")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "Tester")
    _git("config", "commit.gpgsign", "false")
    (repo / "x.txt").write_text("x")
    _git("add", "-A")
    _git("commit", "-q", "-m", "feat(CLM-001): initial")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="CLM-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )

    with transactional(factory) as session:
        commit_link_service.import_from_git_log(session, project_id=p, repo_path=repo)

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "commit_link.imported")
        ).scalar_one()
        assert ev.payload["linked"] == 1


def test_link_resolver_sync_emits_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _new_doc(session, p, "doc-1")
        sec = doc_service.add_section(
            session,
            document_id=doc_id,
            anchor="intro",
            heading="Intro",
            level=1,
            position=0,
            body="See [[doc-1#intro]].",
            author="human:test",
        )
        link_resolver.sync_section(session, sec.row_id)

    with transactional(factory) as session:
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "link.synced",
                    ActivityEventModel.scope_id == str(sec.row_id),
                )
            ).scalars()
        )
        assert len(events) >= 1


def test_repo_index_scan_emits_event(engine_with_schema, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    (repo / "a.py").write_text("def alpha(): pass\n")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        repo_index_service.scan_project(session, project_id=p, repo_path=repo)

    with transactional(factory) as session:
        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "repo_index.scanned")
        ).scalar_one()
        assert ev.payload["files"] >= 1
