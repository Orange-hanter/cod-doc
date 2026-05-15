"""AGT-012: freeze the agent profile response-shape contract.

Если кто-то поменяет shape `agent_pick`'s task card / `agent_report`
ответа / `agent_complete` без обновления orchestrator SKILL — этот тест
поймает регрессию.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import agent_service, task_service


def _seed(session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="ctr", title="P", root_path="/tmp/p", config_json={})
    proj.created = now; proj.updated = now
    session.add(proj); session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="ctr-plan", created=now, last_updated=now)
    session.add(plan); session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec); session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _seed_with_task(engine_with_schema, task_id: str = "CON-001"):  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        task_service.create(
            session, project_id=pid, plan_id=plid, section_id=sid,
            task_id=task_id, title=f"Task {task_id}",
            type=TaskType.FEATURE, priority=Priority.MEDIUM, author="t",
            acceptance="✓ A; ✓ B",
        )
    return factory


# ----------------------------------------------------------------- #
# Task card shape                                                    #
# ----------------------------------------------------------------- #


CARD_TOP_LEVEL = {"task", "context", "navigation"}
CONTEXT_KEYS = {
    "plan", "story", "related_docs", "siblings", "affected_files", "recent_history",
}
NAVIGATION_KEYS = {
    "applicable_skills", "next_actions", "success_criteria", "legal_status_transitions",
}


def test_card_top_level_keys_frozen(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema)
    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="contract")
    assert set(card.keys()) >= CARD_TOP_LEVEL, (
        f"task card lost a top-level key. Got: {sorted(card.keys())}, expected ≥ {sorted(CARD_TOP_LEVEL)}"
    )


def test_card_context_keys_frozen(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema)
    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="contract")
    assert set(card["context"].keys()) == CONTEXT_KEYS, (
        f"card.context keys drifted. Got: {sorted(card['context'])}, expected: {sorted(CONTEXT_KEYS)}"
    )


def test_card_navigation_keys_frozen(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema)
    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="contract")
    assert set(card["navigation"].keys()) == NAVIGATION_KEYS, (
        f"card.navigation keys drifted. Got: {sorted(card['navigation'])}, expected: {sorted(NAVIGATION_KEYS)}"
    )


def test_navigation_skills_carry_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The whole point of agent_pick: skill bodies inlined."""
    factory = _seed_with_task(engine_with_schema)
    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="contract")
    skills = card["navigation"]["applicable_skills"]
    assert skills, "card must inline at least one skill"
    for s in skills:
        assert s.get("body"), f"skill {s.get('name')!r} missing body"


def test_legal_status_transitions_includes_done(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema)
    with transactional(factory) as session:
        card = agent_service.pick(session, project_id=1, agent_id="contract")
    legal = card["navigation"]["legal_status_transitions"]
    assert "done" in legal


# ----------------------------------------------------------------- #
# agent_report dispatcher branches                                   #
# ----------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind", ["progress", "blocker", "approval_request", "needs_context"],
)
def test_agent_report_legal_kinds_return_ok(engine_with_schema, kind) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema, task_id="REPC-001")
    with transactional(factory) as session:
        r = agent_service.report(
            session, project_id=1, task_id="REPC-001",
            kind=kind, message="contract test", agent_id="agent",
        )
    assert r.get("ok") is True, f"kind={kind} should succeed: {r}"
    assert r.get("kind") == kind


def test_agent_report_unknown_kind_lists_legal(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema, task_id="REPC-002")
    with transactional(factory) as session:
        r = agent_service.report(
            session, project_id=1, task_id="REPC-002",
            kind="wrong", message="x", agent_id="agent",
        )
    assert r["ok"] is False
    assert set(r.get("legal_kinds", [])) == {
        "progress", "blocker", "approval_request", "needs_context",
    }


# ----------------------------------------------------------------- #
# agent_complete + agent_release                                      #
# ----------------------------------------------------------------- #


def test_agent_complete_response_shape(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema, task_id="CMPC-001")
    with transactional(factory) as session:
        agent_service.pick(session, project_id=1, agent_id="alpha")
    with transactional(factory) as session:
        r = agent_service.complete(
            session, project_id=1, task_id="CMPC-001", agent_id="alpha",
            commit_sha="abc", summary="ok",
        )
    assert {"ok", "task_id", "status", "commit_sha", "next_actions"} <= set(r.keys())
    assert r["status"] == "done"


def test_agent_release_response_shape(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema, task_id="RELC-001")
    with transactional(factory) as session:
        agent_service.pick(session, project_id=1, agent_id="alpha")
    with transactional(factory) as session:
        r = agent_service.release(
            session, project_id=1, task_id="RELC-001", agent_id="alpha",
            reason="contract test",
        )
    assert {"ok", "task_id", "status", "next_actions"} <= set(r.keys())


# ----------------------------------------------------------------- #
# Idempotency invariant                                              #
# ----------------------------------------------------------------- #


def test_agent_pick_idempotent_same_agent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = _seed_with_task(engine_with_schema, task_id="IDM-001")
    with transactional(factory) as session:
        first = agent_service.pick(session, project_id=1, agent_id="X")
    with transactional(factory) as session:
        second = agent_service.pick(session, project_id=1, agent_id="X")
    assert first["task"]["task_id"] == second["task"]["task_id"]
    assert second.get("idempotent_replay") is True
