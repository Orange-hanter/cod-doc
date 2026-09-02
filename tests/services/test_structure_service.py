"""Blob-first ingest, trust, latest semantics, drift and pinned context."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.infra.models.structure import DocCodeClaimModel, StructureFindingModel
from cod_doc.services.projection_service import detect_drift as detect_projection_drift
from cod_doc.services.structure_context import build_structure_context
from cod_doc.services.structure_drift import (
    apply_waivers,
    compute_structure_drift,
    triage_findings,
    upsert_waiver,
)
from cod_doc.services.structure_obligations import export_obligations, generate_property_drafts
from cod_doc.services.structure_protocol import StructureProtocolError, sha256_text
from cod_doc.services.structure_service import (
    get_latest,
    ingest_structure,
    replay_snapshot,
    require_pinned_snapshot,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "structure"


def _facts() -> dict:
    payload = json.loads((FIXTURES / "structure-facts.v1.json").read_text(encoding="utf-8"))
    payload["facts"]["entities"] = [
        {
            "id": "entity:src/auth.ts#validate",
            "observedId": "entity:src/auth.ts#validate",
            "kind": "function",
            "name": "validate",
            "label": "validate()",
            "path": "src/auth.ts",
            "confidence": "extracted",
        }
    ]
    payload["facts"]["contracts"] = [
        {
            "id": "contract:src/auth.ts#validate",
            "entityRef": "entity:src/auth.ts#validate",
            "name": "validate",
            "kind": "signature",
            "path": "src/auth.ts",
        }
    ]
    payload["facts"]["dependencies"] = [
        {
            "from": "entity:src/login.ts#login",
            "to": "entity:src/auth.ts#validate",
            "relation": "calls",
            "confidence": "extracted",
        }
    ]
    return payload


def _assessment() -> dict:
    payload = json.loads((FIXTURES / "structure-assessment.v1.json").read_text(encoding="utf-8"))
    payload["assessments"]["contractScenarios"] = [
        {
            "obligationRef": "obl:demo#error",
            "contractRefs": ["contract:src/auth.ts#validate"],
            "kind": "error_path",
            "status": "partial",
            "statusReason": "aggregate_lcov_ceiling",
            "evidence": ["lcov:FNDA>0"],
            "missingEvidence": ["per_test_evidence"],
        }
    ]
    payload["hints"] = [
        {
            "ruleId": "scenario.surviving_contract_mutant",
            "priority": "high",
            "subjectRefs": ["contract:src/auth.ts#validate"],
            "summary": "Mutant survived on validate",
            "evidence": ["mutant:1:survived"],
            "missingEvidence": ["killed_mutant"],
            "remediationTarget": "test",
        }
    ]
    return payload


def _project(session) -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="demo", title="Demo", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_ingest_is_idempotent_and_untrusted_does_not_publish(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    with transactional(factory) as session:
        project_id = _project(session)
        first = ingest_structure(
            session,
            project_id,
            facts=facts,
            assessment=_assessment(),
            trust_tier="untrusted",
            project_slug="demo",
        )
        second = ingest_structure(
            session,
            project_id,
            facts=facts,
            assessment=_assessment(),
            trust_tier="untrusted",
            project_slug="demo",
        )
        findings = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        latest = get_latest(session, project_id, branch_ref="main")
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert first["publishedCurrent"] is False
    assert first["snapshot"]["fingerprint"] == second["snapshot"]["fingerprint"]
    assert findings == []
    assert latest is None


def test_trusted_ingest_publishes_latest_main_not_pr(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    main_facts = _facts()
    pr_facts = _facts()
    pr_facts["fingerprint"] = "b" * 64
    pr_facts["provenance"]["isDefaultBranch"] = False
    pr_facts["provenance"]["branchRef"] = "feature/x"
    pr_facts["provenance"]["prNumber"] = 12
    with transactional(factory) as session:
        project_id = _project(session)
        ingest_structure(
            session, project_id, facts=main_facts, trust_tier="trusted_local", project_slug="demo"
        )
        ingest_structure(
            session, project_id, facts=pr_facts, trust_tier="trusted_local", project_slug="demo"
        )
        main = get_latest(session, project_id, branch_ref="main")
        pr = get_latest(session, project_id, pr_number=12)
        with pytest.raises(StructureProtocolError, match="branch context"):
            get_latest(session, project_id)
    assert main is not None
    assert main.pr_number is None
    assert pr is not None
    assert pr.pr_number == 12
    assert pr.fingerprint != main.fingerprint


def test_structure_drift_is_not_projection_drift(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    assessment = _assessment()
    with transactional(factory) as session:
        project_id = _project(session)
        result = ingest_structure(
            session,
            project_id,
            facts=facts,
            assessment=assessment,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        assert result["drift"] is not None
        obligations = export_obligations(session, project_id, project_slug="demo", head_sha="abcdef1")
        drift = compute_structure_drift(
            facts_payload=facts,
            assessment_payload=assessment,
            obligations_export=obligations,
        )
        assert drift["kind"] == "structure_drift"
        assert detect_projection_drift is not compute_structure_drift
        findings = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        assert any(row.rule_id == "scenario.must_obligation_gap" for row in findings)
        assert any(row.rule_id == "scenario.surviving_contract_mutant" for row in findings)


def test_waiver_suppresses_triage_but_not_assessment(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _project(session)
        ingest_structure(
            session,
            project_id,
            facts=_facts(),
            assessment=_assessment(),
            trust_tier="trusted_local",
            project_slug="demo",
        )
        row = session.execute(
            select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
        ).scalars().first()
        assert row is not None
        upsert_waiver(
            session,
            project_id,
            finding_fingerprint=row.fingerprint,
            owner="docs",
            reason="tracked elsewhere",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        waived = apply_waivers(
            session,
            project_id,
            [{"fingerprint": row.fingerprint, "ruleId": row.rule_id, "status": "open", "priority": "high", "summary": row.summary, "missingEvidence": [], "remediationTarget": "test"}],
        )
        assert waived[0]["suppressed"] is True
        assert triage_findings(waived) == []
        expired = apply_waivers(
            session,
            project_id,
            waived,
            now=datetime.now(UTC) + timedelta(days=40),
        )
        assert expired[0]["suppressed"] is False


def test_context_requires_pin_and_stays_bounded(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    with transactional(factory) as session:
        project_id = _project(session)
        ingest_structure(
            session, project_id, facts=facts, trust_tier="trusted_local", project_slug="demo"
        )
        with pytest.raises(StructureProtocolError, match="headSha or snapshotFingerprint"):
            require_pinned_snapshot(session, project_id)
        context = build_structure_context(
            session,
            project_id,
            project_slug="demo",
            head_sha="abcdef1",
            scope_refs=["entity:src/auth.ts#validate"],
        )
        assert context["pinned"]["headSha"] == "abcdef1"
        assert context["bytes"] <= 32 * 1024
        assert context["tokenEstimate"] >= 1


def test_obligations_export_and_property_drafts(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)
    with transactional(factory) as session:
        project_id = _project(session)
        session.add(
            DocCodeClaimModel(
                project_id=project_id,
                claim_id="claim:range",
                kind="scenario",
                status="confirmed",
                content_hash=sha256_text("range"),
                subject_ref="contract:src/auth.ts#validate",
                expected_json={"kind": "boundary_value"},
                provenance="manual",
                created=now,
                updated=now,
            )
        )
        payload = export_obligations(session, project_id, project_slug="demo", head_sha="abcdef1")
        assert payload["kind"] == "obligations_export"
        assert payload["version"] == 1
        drafts = generate_property_drafts(
            [
                {
                    "id": "obl:range",
                    "status": "confirmed",
                    "kind": "boundary_value",
                    "statement": "MUST accept values from 0 through 10",
                    "contentHash": sha256_text("range"),
                },
                {
                    "id": "obl:draft",
                    "status": "draft",
                    "kind": "invariant",
                    "statement": "draft",
                    "contentHash": sha256_text("draft"),
                },
            ]
        )
        assert len(drafts) == 1
        assert "below_minimum" in drafts[0]["generators"]
        assert drafts[0]["requiresConfirmation"] is True


def test_link_suggest_confirm_and_bootstrap_drafts(engine_with_schema) -> None:
    from cod_doc.services.structure_drift import (
        confirm_obligation_link,
        persist_link_suggestions,
        suggest_obligation_links,
    )

    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    with transactional(factory) as session:
        project_id = _project(session)
        result = ingest_structure(
            session, project_id, facts=facts, trust_tier="trusted_local", project_slug="demo"
        )
        assert int(result["draftClaims"]) >= 1
        suggestions = suggest_obligation_links(
            [
                {
                    "id": "obl:validate",
                    "statement": "MUST call validate() before persisting a session",
                    "docRef": "specs/auth.md",
                    "contractRefs": [],
                    "status": "confirmed",
                }
            ],
            facts,
        )
        assert suggestions[0]["resolution"] in {"unique", "ambiguous"}
        assert suggestions[0]["candidates"]
        persist_link_suggestions(session, project_id, suggestions)
        confirmed = confirm_obligation_link(
            session,
            project_id,
            obligation_ref="obl:validate",
            contract_ref="contract:src/auth.ts#validate",
            author="human",
        )
        exported = export_obligations(session, project_id, project_slug="demo", head_sha="abcdef1")
    assert confirmed["status"] == "confirmed"
    assert any(item["id"] == confirmed["claimId"] for item in exported["obligations"])


def _hex(digit: str) -> str:
    return digit * 64


def test_replay_rebuilds_indexes_from_stored_blob(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    with transactional(factory) as session:
        project_id = _project(session)
        result = ingest_structure(
            session, project_id, facts=facts, trust_tier="trusted_local", project_slug="demo"
        )
        snapshot_id = int(result["snapshot"]["snapshotId"])
        first = dict(result["indexes"])
        replayed = replay_snapshot(session, project_id, snapshot_id)
    assert replayed["indexes"] == first
    assert int(replayed["indexes"]["entities"]) >= 1


def test_finding_goes_pending_verify_then_resolves_when_gap_stays_gone(engine_with_schema) -> None:
    factory = make_session_factory(engine_with_schema)
    facts = _facts()
    assessment = _assessment()
    with transactional(factory) as session:
        project_id = _project(session)
        ingest_structure(
            session,
            project_id,
            facts=facts,
            assessment=assessment,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        open_rows = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        assert open_rows
        assert all(row.status == "open" for row in open_rows)

        closed_assessment = copy.deepcopy(assessment)
        closed_assessment["assessments"]["contractScenarios"] = [
            {
                **closed_assessment["assessments"]["contractScenarios"][0],
                "status": "covered",
                "statusReason": "test link, execution and scenario-specific evidence satisfied",
                "missingEvidence": [],
            }
        ]
        closed_assessment["hints"] = []

        second_facts = copy.deepcopy(facts)
        second_facts["fingerprint"] = _hex("a")
        closed_assessment["fingerprint"] = _hex("b")
        closed_assessment["factsFingerprint"] = second_facts["fingerprint"]
        ingest_structure(
            session,
            project_id,
            facts=second_facts,
            assessment=closed_assessment,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        pending = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        assert pending
        assert all(row.status == "pending_verify" for row in pending)

        third_facts = copy.deepcopy(facts)
        third_facts["fingerprint"] = _hex("c")
        third_assessment = copy.deepcopy(closed_assessment)
        third_assessment["fingerprint"] = _hex("d")
        third_assessment["factsFingerprint"] = third_facts["fingerprint"]
        ingest_structure(
            session,
            project_id,
            facts=third_facts,
            assessment=third_assessment,
            trust_tier="trusted_local",
            project_slug="demo",
        )
        resolved = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
    assert resolved
    assert all(row.status == "resolved" for row in resolved)
    assert all(row.resolved_by_snapshot_id is not None for row in resolved)
