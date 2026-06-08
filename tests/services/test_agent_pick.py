"""AGT-003: agent_pick — atomic task+context+navigation card."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import agent_service, task_service


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="agpck", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="agpck-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(session, pid, plid, sid, tid, *, prio=Priority.MEDIUM, acceptance=None):
    return task_service.create(
        session,
        project_id=pid,
        plan_id=plid,
        section_id=sid,
        task_id=tid,
        title=f"Implement {tid}",
        type=TaskType.FEATURE,
        priority=prio,
        author="t",
        acceptance=acceptance,
    )


def test_agent_pick_returns_full_card(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(
            session,
            pid,
            plid,
            sid,
            "APK-001",
            prio=Priority.HIGH,
            acceptance="✓ A done; ✓ B done; ✓ C done",
        )
        _make(session, pid, plid, sid, "APK-002")

    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="agent-alpha")

    # Top-level shape.
    assert "task" in card and card["task"] is not None
    assert "context" in card
    assert "navigation" in card

    # Task: highest-priority ready (APK-001 high prio beats APK-002 medium).
    assert card["task"]["task_id"] == "APK-001"
    assert card["task"]["status"] == "in-progress"  # post-checkout

    # Context block.
    ctx = card["context"]
    assert ctx["plan"]["scope"] == "agpck-plan"
    assert ctx["plan"]["section_letter"] == "A"
    assert ctx["story"] is None  # no story linked
    assert isinstance(ctx["related_docs"], list)
    assert isinstance(ctx["siblings"], list)
    assert isinstance(ctx["affected_files"], list)
    assert isinstance(ctx["recent_history"], list)

    # Navigation block.
    nav = card["navigation"]
    skills = nav["applicable_skills"]
    assert isinstance(skills, list) and skills, "must inline at least one skill"
    # Each skill carries name + description + body (not just name).
    for s in skills:
        assert {"name", "description", "body"} <= set(s.keys()), f"skill missing body: {s}"
        assert isinstance(s["body"], str) and s["body"], f"skill {s['name']} has empty body"

    # Parsed acceptance into checklist (3 items from ✓).
    assert len(nav["success_criteria"]) == 3
    assert all("done" in c for c in nav["success_criteria"])

    # Legal transitions from in_progress.
    legal = nav["legal_status_transitions"]
    assert "done" in legal
    assert "blocked" in legal


def test_agent_pick_skips_blocked(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        a = _make(session, pid, plid, sid, "APK-001", prio=Priority.HIGH)
        b = _make(session, pid, plid, sid, "APK-002")
        session.add(DependencyModel(from_task_id=b.row_id, to_task_id=a.row_id, kind="blocks"))
        session.flush()

    with transactional(factory) as session:
        # Mark A checked-out by someone else.
        a_row = session.execute(
            select(TaskModel).where(TaskModel.task_id == "APK-001")
        ).scalar_one()
        a_row.checked_out_by = "another-agent"
        a_row.checked_out_at = datetime.now(UTC)
        session.flush()

    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="me")
    # A is locked; B is blocked by A; nothing ready.
    assert card["task"] is None
    assert card["reason"] == "no_ready_tasks"


def test_agent_pick_is_idempotent_for_same_agent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "APK-001", prio=Priority.HIGH)
        _make(session, pid, plid, sid, "APK-002")

    with transactional(factory) as session:
        first = agent_service.pick(session, project_id=1, agent_id="X")
    with transactional(factory) as session:
        second = agent_service.pick(session, project_id=1, agent_id="X")

    assert first["task"]["task_id"] == second["task"]["task_id"]
    assert second.get("idempotent_replay") is True


def test_agent_pick_empty_returns_structured_reason(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="me")
    assert card == {"task": None, "reason": "no_ready_tasks"}


def test_agent_pick_ignores_stale_lock_on_done_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """F2 regression (2026-05-15 audit): a done task with a dangling
    ``checked_out_by`` must not replay as ``idempotent_replay`` — the next
    pick should pick a fresh ready task instead.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "APK-100", prio=Priority.HIGH)
        _make(session, pid, plid, sid, "APK-101", prio=Priority.MEDIUM)
    # Mark APK-100 as done WHILE keeping the lock — simulates a SQL bypass.
    with transactional(factory) as session:
        row = session.execute(select(TaskModel).where(TaskModel.task_id == "APK-100")).scalar_one()
        row.status = "done"
        row.checked_out_by = "stale-agent"
        row.checked_out_at = datetime.now(UTC)
    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="stale-agent")
    # Must NOT replay APK-100; must pick the still-open APK-101.
    assert card["task"]["task_id"] == "APK-101"
    assert card.get("idempotent_replay") is not True
