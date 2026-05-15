"""AGT-004/005/006/007: agent_get, agent_report, agent_complete, agent_release."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from cod_doc.domain.entities import (
    DocumentStatus, DocumentType, Priority, Sensitivity, TaskType,
    UserStory, UserStoryStatus,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel, PlanSectionModel, ProjectModel, TaskModel,
)
from cod_doc.infra.repositories import UserStoryRepository
from cod_doc.services import agent_service, doc_service, task_service


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="agwf", title="P", root_path="/tmp/p", config_json={})
    proj.created = now; proj.updated = now
    session.add(proj); session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="agwf-plan", created=now, last_updated=now)
    session.add(plan); session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec); session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make_task(session, pid, plid, sid, tid):
    return task_service.create(
        session, project_id=pid, plan_id=plid, section_id=sid,
        task_id=tid, title=f"Task {tid}",
        type=TaskType.FEATURE, priority=Priority.MEDIUM, author="t",
    )


# ----------------------------------------------------------------- #
# AGT-004: agent_get                                                 #
# ----------------------------------------------------------------- #


def test_agent_get_unknown_what_returns_legal_list(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with transactional(factory) as session:
        r = agent_service.get(session, project_id=1, task_id="x", what="weird")
    assert r["found"] is False
    assert "legal_what" in r
    assert "full_doc_body" in r["legal_what"]


def test_agent_get_full_doc_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        doc_service.create(
            session, project_id=pid, doc_key="guide/x", title="Guide X",
            type=DocumentType.GUIDE, status=DocumentStatus.DRAFT,
            sensitivity=Sensitivity.INTERNAL, author="t",
            preamble="Hello body content",
        )
    with transactional(factory) as session:
        r = agent_service.get(session, project_id=1, task_id="X", what="full_doc_body", ref="guide/x")
    assert r["found"] is True
    assert "body" in r["payload"]


def test_agent_get_story_full(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _, _ = _seed(session)
        UserStoryRepository(session).add(UserStory(
            project_id=pid, story_id="US-1", persona="dev",
            narrative="want X", status=UserStoryStatus.DRAFT,
            priority=Priority.MEDIUM,
        ))
    with transactional(factory) as session:
        r = agent_service.get(session, project_id=1, task_id="X", what="story_full", ref="US-1")
    assert r["found"] is True
    assert r["payload"]["story_id"] == "US-1"
    assert "acceptance_criteria" in r["payload"]


# ----------------------------------------------------------------- #
# AGT-005: agent_report                                              #
# ----------------------------------------------------------------- #


def test_agent_report_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "REP-001")
    with transactional(factory) as session:
        r = agent_service.report(
            session, project_id=1, task_id="REP-001",
            kind="progress", message="halfway done", agent_id="alpha",
        )
    assert r["ok"] is True
    assert r["kind"] == "progress"


def test_agent_report_blocker_sets_blocked_reason(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "REP-002")
    with transactional(factory) as session:
        r = agent_service.report(
            session, project_id=1, task_id="REP-002",
            kind="blocker", message="waiting on stakeholder", agent_id="alpha",
        )
    assert r["ok"] is True
    with transactional(factory) as session:
        row = session.execute(
            select(TaskModel).where(TaskModel.task_id == "REP-002")
        ).scalar_one()
    assert row.status == "blocked"
    assert "stakeholder" in (row.blocked_reason or "")


def test_agent_report_unknown_kind_returns_legal_list(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "REP-003")
    with transactional(factory) as session:
        r = agent_service.report(
            session, project_id=1, task_id="REP-003",
            kind="weird", message="x", agent_id="alpha",
        )
    assert r["ok"] is False
    assert "progress" in r["legal_kinds"]


# ----------------------------------------------------------------- #
# AGT-006 + AGT-007: agent_complete + agent_release                   #
# ----------------------------------------------------------------- #


def test_agent_complete_marks_done_and_releases(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "CMP-001")
    # First pick it (this transitions to in_progress + sets checkout).
    with transactional(factory) as session:
        agent_service.pick(session, project_id=1, agent_id="alpha")
    # Now complete.
    with transactional(factory) as session:
        r = agent_service.complete(
            session, project_id=1, task_id="CMP-001", agent_id="alpha",
            commit_sha="abc123", summary="done",
        )
    assert r["ok"] is True
    assert r["status"] == "done"
    with transactional(factory) as session:
        row = session.execute(
            select(TaskModel).where(TaskModel.task_id == "CMP-001")
        ).scalar_one()
    assert row.status == "done"
    assert row.checked_out_by is None  # lock released


def test_agent_release_drops_lock_without_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make_task(session, pid, plid, sid, "REL-001")
    with transactional(factory) as session:
        agent_service.pick(session, project_id=1, agent_id="alpha")
    with transactional(factory) as session:
        r = agent_service.release(
            session, project_id=1, task_id="REL-001", agent_id="alpha",
            reason="rethinking approach",
        )
    assert r["ok"] is True
    with transactional(factory) as session:
        row = session.execute(
            select(TaskModel).where(TaskModel.task_id == "REL-001")
        ).scalar_one()
    assert row.checked_out_by is None
    assert row.status != "done"
