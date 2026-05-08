"""PCA-121: ApprovalService — first-class decision gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ApprovalModel, ProjectModel
from cod_doc.services import approval_service as approvals
from cod_doc.services.run_context import set_current_run_id

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session, slug: str = "ap") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_request_creates_pending_approval(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(
            session, proj_id,
            approval_type="plan_review",
            requested_by="orchestrator-run-X",
            payload={"title": "Approve plan", "summary": "..."},
            linked_task_refs=["PCA-100"],
        )
        assert a.status == "pending"
        assert a.approval_type == "plan_review"
        assert a.linked_task_refs == ["PCA-100"]
        assert a.expires_at is not None  # default TTL


def test_request_validates_approval_type(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(ValueError, match="Invalid approval_type"):
            approvals.request(
                session, proj_id, approval_type="not_a_real_type",
                requested_by="x",
            )


def test_request_no_expiry_when_hours_is_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(
            session, proj_id, approval_type="manual",
            requested_by="x", expires_in_hours=None,
        )
        assert a.expires_at is None


def test_request_auto_cancels_existing_pending_on_same_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a1 = approvals.request(
            session, proj_id, approval_type="plan_review",
            requested_by="run-1", linked_task_refs=["T-001"],
        )
        a2 = approvals.request(
            session, proj_id, approval_type="plan_review",
            requested_by="run-2", linked_task_refs=["T-001"],
        )

    with transactional(factory) as session:
        a1_reloaded = approvals.get(session, proj_id, a1.approval_id)
        a2_reloaded = approvals.get(session, proj_id, a2.approval_id)
        assert a1_reloaded.status == "cancelled"
        assert a1_reloaded.decision_comment == "superseded"
        assert a2_reloaded.status == "pending"


def test_request_does_not_cancel_unrelated_pending(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a1 = approvals.request(
            session, proj_id, approval_type="plan_review",
            requested_by="run-1", linked_task_refs=["T-001"],
        )
        approvals.request(
            session, proj_id, approval_type="plan_review",
            requested_by="run-2", linked_task_refs=["T-002"],
        )

    with transactional(factory) as session:
        a1_reloaded = approvals.get(session, proj_id, a1.approval_id)
        assert a1_reloaded.status == "pending"


def test_request_stamps_run_id_from_contextvar(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        try:
            set_current_run_id("01J0APPROVE")
            a = approvals.request(
                session, proj_id, approval_type="manual", requested_by="x",
            )
            assert a.run_id == "01J0APPROVE"
        finally:
            set_current_run_id(None)


def test_get_returns_none_for_unknown_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        assert approvals.get(session, proj_id, "no-such-approval") is None


def test_list_filters_by_status_and_type(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a1 = approvals.request(session, proj_id, approval_type="plan_review",
                               requested_by="x")
        a2 = approvals.request(session, proj_id, approval_type="risky_action",
                               requested_by="x")
        approvals.resolve(session, proj_id, a1.approval_id,
                          decision="approve", resolved_by="op")

    with transactional(factory) as session:
        pending = approvals.list_approvals(session, proj_id, status="pending")
        assert pending["total"] == 1
        risky = approvals.list_approvals(session, proj_id,
                                         approval_type="risky_action")
        assert risky["total"] == 1


def test_resolve_approve_sets_status_and_returns_wake_hint(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(
            session, proj_id, approval_type="plan_review",
            requested_by="run-X",
            linked_task_refs=["PCA-100", "PCA-101"],
        )
        result = approvals.resolve(
            session, proj_id, a.approval_id,
            decision="approve", resolved_by="human:dakh", comment="LGTM",
        )
        assert result["approval"]["status"] == "approved"
        assert result["approval"]["resolved_by"] == "human:dakh"
        assert result["approval"]["decision_comment"] == "LGTM"
        # wake hint targets first linked task.
        assert result["wake_hint"] == {
            "reason": "approval_resolved",
            "task_id": "PCA-100",
            "approval_id": a.approval_id,
            "decision": "approve",
            "comment": "LGTM",
        }


def test_resolve_deny_works_and_carries_comment(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(
            session, proj_id, approval_type="risky_action",
            requested_by="run-X", linked_task_refs=["T-001"],
        )
        result = approvals.resolve(
            session, proj_id, a.approval_id,
            decision="deny", resolved_by="op", comment="too risky",
        )
        assert result["approval"]["status"] == "denied"
        assert result["wake_hint"]["decision"] == "deny"


def test_resolve_returns_no_wake_hint_when_no_linked_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(session, proj_id, approval_type="manual",
                              requested_by="x")
        result = approvals.resolve(session, proj_id, a.approval_id,
                                   decision="approve", resolved_by="op")
        assert result["wake_hint"] is None


def test_resolve_invalid_decision_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(session, proj_id, approval_type="manual",
                              requested_by="x")
        with pytest.raises(ValueError, match="must be 'approve' or 'deny'"):
            approvals.resolve(session, proj_id, a.approval_id,
                              decision="maybe", resolved_by="op")


def test_resolve_already_resolved_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(session, proj_id, approval_type="manual",
                              requested_by="x")
        approvals.resolve(session, proj_id, a.approval_id,
                          decision="approve", resolved_by="op")
        with pytest.raises(ValueError, match="already"):
            approvals.resolve(session, proj_id, a.approval_id,
                              decision="approve", resolved_by="op2")


def test_resolve_unknown_id_raises_lookup_error(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        with pytest.raises(LookupError):
            approvals.resolve(session, proj_id, "no-such",
                              decision="approve", resolved_by="op")


def test_cancel_pending_works(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(session, proj_id, approval_type="manual",
                              requested_by="x")
        cancelled = approvals.cancel(
            session, proj_id, a.approval_id,
            reason="changed mind", cancelled_by="op",
        )
        assert cancelled.status == "cancelled"
        assert cancelled.decision_comment == "changed mind"
        assert cancelled.resolved_by == "op"


def test_cancel_already_resolved_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(session, proj_id, approval_type="manual",
                              requested_by="x")
        approvals.resolve(session, proj_id, a.approval_id,
                          decision="approve", resolved_by="op")
        with pytest.raises(ValueError, match="Only pending"):
            approvals.cancel(session, proj_id, a.approval_id, reason="r")


def test_persisted_payload_and_doc_revision_links(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        a = approvals.request(
            session, proj_id, approval_type="plan_review",
            requested_by="run-X",
            payload={"summary": "deploy v2", "risks": ["downtime"]},
            linked_doc_revision_ids=["01J0REVA", "01J0REVB"],
        )

    with transactional(factory) as session:
        loaded = approvals.get(session, proj_id, a.approval_id)
        assert loaded.payload["summary"] == "deploy v2"
        assert loaded.payload["risks"] == ["downtime"]
        assert loaded.linked_doc_revision_ids == ["01J0REVA", "01J0REVB"]


def test_request_default_ttl_is_48_hours(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        before = datetime.now(UTC)
        a = approvals.request(session, proj_id, approval_type="manual",
                              requested_by="x")
        delta = a.expires_at - before
        # Allow loose bounds for clock skew during the test.
        assert timedelta(hours=47, minutes=55) < delta < timedelta(hours=48, minutes=5)
