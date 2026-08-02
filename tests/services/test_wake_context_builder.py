"""PCA-021: build_wake_context() composition over heartbeat_service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.agent.wake_context import WakeReason, build_wake_context
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_plan(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="wb-proj", title="WC Proj", root_path="/tmp/wb", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="wb-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Phase 1", slug="A-Phase-1", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def test_build_cold_start_empty_payload(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        wc = build_wake_context(session, reason=WakeReason.COLD_START)

    assert wc.reason is WakeReason.COLD_START
    assert wc.payload == {}
    assert wc.skills_to_preload == ["orchestrator"]


def test_build_manual_with_custom_skills(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        wc = build_wake_context(
            session,
            reason=WakeReason.MANUAL,
            skills_to_preload=["validation", "audit-cadence"],
        )

    assert wc.reason is WakeReason.MANUAL
    assert wc.payload == {}
    assert wc.skills_to_preload == ["validation", "audit-cadence"]


def test_build_task_assigned_uses_heartbeat_payload(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="WB-001",
            title="t",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="x",
        )
        wc = build_wake_context(session, reason=WakeReason.TASK_ASSIGNED, task_id="WB-001")

    assert wc.reason is WakeReason.TASK_ASSIGNED
    assert wc.task_id == "WB-001"
    assert wc.payload["task"]["id"] == "WB-001"
    assert "ancestry" in wc.payload
    assert "active_skills_hint" in wc.payload


def test_build_approval_resolved_uses_heartbeat_payload(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed_plan(session)
        task_service.create(
            session,
            project_id=p,
            plan_id=pl,
            section_id=s,
            task_id="WB-002",
            title="approved task",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="x",
        )
        wc = build_wake_context(session, reason=WakeReason.APPROVAL_RESOLVED, task_id="WB-002")

    assert wc.reason is WakeReason.APPROVAL_RESOLVED
    assert wc.payload["task"]["id"] == "WB-002"


def test_build_doc_drift_payload_contains_trigger(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        wc = build_wake_context(
            session,
            reason=WakeReason.DOC_DRIFT,
            triggering_doc_ref="doc:specs_modules_md",
            triggering_revision_id="01J0000",
        )

    assert wc.reason is WakeReason.DOC_DRIFT
    assert wc.payload == {
        "doc_ref": "doc:specs_modules_md",
        "since_revision_id": "01J0000",
    }


def test_build_approval_resolved_without_task_id_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(ValueError, match="requires task_id"):
        build_wake_context(session, reason=WakeReason.APPROVAL_RESOLVED)


def test_build_doc_drift_without_doc_ref_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with (
        transactional(factory) as session,
        pytest.raises(ValueError, match="requires triggering_doc_ref"),
    ):
        build_wake_context(session, reason=WakeReason.DOC_DRIFT)


def test_build_unknown_task_id_propagates(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(task_service.TaskNotFoundError):
        build_wake_context(session, reason=WakeReason.TASK_ASSIGNED, task_id="NOPE-001")
