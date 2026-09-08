"""Structure drift — separate from DB↔Markdown projection drift.

Findings are structure-specific and are not ingested as ``ai_review`` findings.
Untrusted snapshots must not call these functions.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models.structure import (
    DocCodeClaimModel,
    StructureFindingModel,
    StructureLinkSuggestionModel,
    StructureWaiverModel,
)
from cod_doc.services.structure_protocol import (
    as_int,
    as_list,
    as_object,
    finding_fingerprint,
    sha256_text,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session


_PATH_TOKEN_MIN = 3
_UNIQUE_SCORE = 6
_INFERRED_SCORE = 3
_LABEL_SCORE = 5
_PATH_OVERLAP_SCORE = 2
_SIGNATURE_SCORE = 1
_HINT_RULES = frozenset(
    {
        "coverage.function_unexecuted_high_fan_in",
        "coverage.cross_boundary_target_unexecuted",
        "scenario.surviving_contract_mutant",
    }
)


def _normalized(value: object) -> str:
    return re.sub(r"\(\)$", "", str(value or "")).lstrip(".").lower()


def _payload_facts(facts_payload: dict[str, object]) -> dict[str, object]:
    return as_object(facts_payload.get("facts") or {}, label="facts")


def suggest_obligation_links(
    obligations: Sequence[object],
    facts_payload: dict[str, object],
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    facts = _payload_facts(facts_payload)
    entities = {
        str(item.get("observedId") or item.get("id") or ""): item
        for item in as_list(facts.get("entities"), label="entities")
        if isinstance(item, dict)
    }
    contracts = [
        item
        for item in as_list(facts.get("contracts"), label="contracts")
        if isinstance(item, dict)
    ]
    suggestions: list[dict[str, object]] = []
    for raw in obligations:
        obligation = as_object(raw, label="obligation")
        refs = as_list(obligation.get("contractRefs") or [], label="contractRefs")
        if refs:
            continue
        statement = _normalized(obligation.get("statement"))
        doc_path = _normalized(obligation.get("docRef"))
        candidates: list[dict[str, object]] = []
        for contract in contracts:
            entity = entities.get(str(contract.get("entityRef") or ""))
            label = _normalized(
                (entity or {}).get("label") or (entity or {}).get("name") or contract.get("name")
            )
            score = 0
            reasons: list[str] = []
            if label and re.search(rf"\b{re.escape(label)}\b", statement):
                score += _LABEL_SCORE
                reasons.append("symbol-mentioned")
            entity_path = str((entity or {}).get("path") or "")
            if entity_path and doc_path:
                overlap = any(
                    part and len(part) > _PATH_TOKEN_MIN - 1 and part in entity_path.lower()
                    for part in doc_path.split("/")
                )
                if overlap:
                    score += _PATH_OVERLAP_SCORE
                    reasons.append("document-path-overlap")
            if str(contract.get("kind") or "") in {"signature", "implements", "method"}:
                score += _SIGNATURE_SCORE
                reasons.append("semantic-signature")
            if score <= 0:
                continue
            candidates.append(
                {
                    "contractRef": str(contract.get("id") or ""),
                    "entityRef": str(contract.get("entityRef") or ""),
                    "score": score,
                    "confidence": (
                        "extracted"
                        if score >= _UNIQUE_SCORE
                        else "inferred"
                        if score >= _INFERRED_SCORE
                        else "heuristic"
                    ),
                    "reasons": reasons,
                }
            )
        candidates.sort(
            key=lambda item: (-as_int(item["score"], label="score"), str(item["contractRef"]))
        )
        trimmed = candidates[:limit]
        resolution = "unresolved"
        if len(trimmed) == 1 and as_int(trimmed[0]["score"], label="score") >= _UNIQUE_SCORE:
            resolution = "unique"
        elif trimmed:
            resolution = "ambiguous"
        suggestions.append(
            {
                "obligationRef": str(obligation.get("id") or ""),
                "candidates": trimmed,
                "resolution": resolution,
            }
        )
    suggestions.sort(key=lambda item: str(item["obligationRef"]))
    return suggestions


def compute_structure_drift(
    *,
    facts_payload: dict[str, object],
    assessment_payload: dict[str, object] | None,
    obligations_export: dict[str, object],
    confirmed_claims: Sequence[object] | None = None,
    link_suggestions: Sequence[object] | None = None,
) -> dict[str, object]:
    """Deterministic structure/docs/scenario drift. Not projection drift."""
    facts = _payload_facts(facts_payload)
    provenance = as_object(facts_payload.get("provenance") or {}, label="provenance")
    contract_ids = {
        str(item.get("id") or "")
        for item in as_list(facts.get("contracts"), label="contracts")
        if isinstance(item, dict)
    }
    entity_ids = {
        str(item.get("observedId") or item.get("id") or "")
        for item in as_list(facts.get("entities"), label="entities")
        if isinstance(item, dict)
    }
    obligations = [
        as_object(item, label="obligation")
        for item in as_list(obligations_export.get("obligations"), label="obligations")
    ]
    suggestions = list(link_suggestions or suggest_obligation_links(obligations, facts_payload))
    suggestion_by = {
        str(item.get("obligationRef")): item for item in suggestions if isinstance(item, dict)
    }
    snapshot_fp = str(
        (assessment_payload or {}).get("fingerprint") or facts_payload.get("fingerprint") or ""
    )
    findings: list[dict[str, object]] = []
    stale = _stale_graph_finding(provenance, snapshot_fp)
    if stale is not None:
        findings.append(stale)
    findings.extend(
        _obligation_findings(obligations, suggestion_by, contract_ids, entity_ids, snapshot_fp)
    )
    findings.extend(_claim_findings(confirmed_claims or [], contract_ids, entity_ids, snapshot_fp))
    if assessment_payload:
        findings.extend(_scenario_gap_findings(assessment_payload, snapshot_fp))
        findings.extend(_hint_findings(assessment_payload, snapshot_fp))
    findings.sort(key=lambda item: str(item["fingerprint"]))
    return {
        "version": 1,
        "kind": "structure_drift",
        "factsFingerprint": facts_payload.get("fingerprint"),
        "assessmentFingerprint": (assessment_payload or {}).get("fingerprint"),
        "obligationsRevision": obligations_export.get("revision") or "none",
        "temporalAlignment": (assessment_payload or {}).get("temporalAlignment") or "unknown",
        "findings": findings,
        "linkSuggestions": suggestions,
    }


def _stale_graph_finding(
    provenance: dict[str, object], snapshot_fp: str
) -> dict[str, object] | None:
    if str(provenance.get("graphStatus") or "fresh") == "fresh":
        return None
    return _finding(
        "structure.graph_stale",
        ["structure:graph"],
        priority="info",
        remediation="code",
        summary="Rebuild the structure graph at the pinned commit.",
        evidence=[f"graph-status:{provenance.get('graphStatus')}"],
        missing=["fresh_graph"],
        snapshot_fp=snapshot_fp,
    )


def _obligation_findings(
    obligations: Sequence[object],
    suggestion_by: Mapping[str, object],
    contract_ids: set[str],
    entity_ids: set[str],
    snapshot_fp: str,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for raw in obligations:
        obligation = as_object(raw, label="obligation")
        if obligation.get("status") != "confirmed":
            continue
        refs = [
            str(item)
            for item in as_list(obligation.get("contractRefs") or [], label="contractRefs")
        ]
        obl_id = str(obligation.get("id") or "")
        if not refs:
            suggestion = as_object(suggestion_by.get(obl_id) or {}, label="suggestion")
            candidates = as_list(suggestion.get("candidates") or [], label="candidates")
            findings.append(
                _finding(
                    "docs.obligation_unlinked",
                    [obl_id],
                    remediation="claim",
                    summary=f"Confirm a code contract link for {obl_id}.",
                    evidence=[
                        f"candidate:{as_object(c, label='c').get('contractRef')}"
                        for c in candidates
                        if isinstance(c, dict)
                    ],
                    missing=["confirmed_contract_link"],
                    snapshot_fp=snapshot_fp,
                )
            )
        elif not any(ref in contract_ids or ref in entity_ids for ref in refs):
            findings.append(
                _finding(
                    "docs.contract_link_broken",
                    [obl_id, *refs],
                    priority="high",
                    remediation="docs",
                    summary=f"Documented contract link for {obl_id} does not exist in the pinned snapshot.",
                    evidence=[f"obligation:{obl_id}:{obligation.get('contentHash')}"],
                    missing=["existing_contract"],
                    snapshot_fp=snapshot_fp,
                )
            )
    return findings


def _claim_findings(
    confirmed_claims: Sequence[object],
    contract_ids: set[str],
    entity_ids: set[str],
    snapshot_fp: str,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for raw_claim in confirmed_claims:
        claim = as_object(raw_claim, label="claim")
        if claim.get("status") != "confirmed":
            continue
        subject = str(claim.get("subject_ref") or claim.get("subjectRef") or "")
        kind = str(claim.get("kind") or "")
        exists = (
            subject in entity_ids
            if kind == "entity_exists"
            else subject in contract_ids or subject in entity_ids
        )
        if exists:
            continue
        findings.append(
            _finding(
                "contract.confirmed_claim_drift",
                [str(claim.get("claim_id") or claim.get("id") or ""), subject],
                priority="high",
                remediation="docs",
                summary=f"Confirmed {kind} claim no longer resolves to observed code.",
                evidence=[f"claim:{claim.get('claim_id')}:{claim.get('content_hash') or ''}"],
                missing=["observed_subject"],
                snapshot_fp=snapshot_fp,
            )
        )
    return findings


def _scenario_gap_findings(
    assessment_payload: dict[str, object], snapshot_fp: str
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    assessments = as_object(assessment_payload.get("assessments") or {}, label="assessments")
    for raw in as_list(assessments.get("contractScenarios") or [], label="contractScenarios"):
        if not isinstance(raw, dict):
            continue
        scenario = as_object(raw, label="scenario")
        status = str(scenario.get("status") or "")
        if status not in {"missing", "partial"}:
            continue
        findings.append(
            _finding(
                "scenario.must_obligation_gap",
                [
                    str(scenario.get("obligationRef") or ""),
                    *[
                        str(item)
                        for item in as_list(scenario.get("contractRefs") or [], label="cr")
                    ],
                ],
                priority="high" if status == "missing" else "medium",
                remediation="test",
                summary=(
                    f"Scenario {scenario.get('kind')} is {status}: "
                    f"{scenario.get('statusReason') or scenario.get('reason') or status}"
                ),
                evidence=[
                    str(item) for item in as_list(scenario.get("evidence") or [], label="ev")
                ],
                missing=[
                    str(item) for item in as_list(scenario.get("missingEvidence") or [], label="me")
                ],
                snapshot_fp=snapshot_fp,
            )
        )
    return findings


def _hint_findings(
    assessment_payload: dict[str, object], snapshot_fp: str
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for raw in as_list(assessment_payload.get("hints") or [], label="hints"):
        if not isinstance(raw, dict):
            continue
        hint = as_object(raw, label="hint")
        rule = str(hint.get("ruleId") or hint.get("id") or "")
        if rule not in _HINT_RULES:
            continue
        findings.append(
            _finding(
                rule,
                [str(item) for item in as_list(hint.get("subjectRefs") or [], label="sr")],
                priority=str(hint.get("priority") or "medium"),
                remediation=str(hint.get("remediationTarget") or "test"),
                summary=str(hint.get("summary") or rule),
                evidence=[str(item) for item in as_list(hint.get("evidence") or [], label="hev")],
                missing=[
                    str(item) for item in as_list(hint.get("missingEvidence") or [], label="hme")
                ],
                snapshot_fp=snapshot_fp,
            )
        )
    return findings


def _finding(
    rule_id: str,
    subject_refs: Sequence[str],
    *,
    priority: str = "medium",
    remediation: str,
    summary: str,
    evidence: Sequence[str],
    missing: Sequence[str],
    snapshot_fp: str,
) -> dict[str, object]:
    refs = sorted({item for item in subject_refs if item})
    fingerprint = finding_fingerprint(rule_id, refs)
    return {
        "id": fingerprint,
        "fingerprint": fingerprint,
        "ruleId": rule_id,
        "priority": priority,
        "status": "open",
        "subjectRefs": refs,
        "remediationTarget": remediation,
        "summary": summary,
        "evidence": sorted(set(evidence)),
        "missingEvidence": sorted(set(missing)),
        "firstSeenSnapshot": snapshot_fp,
        "lastSeenSnapshot": snapshot_fp,
        "resolvedBySnapshot": None,
    }


def persist_link_suggestions(
    session: Session,
    project_id: int,
    suggestions: Sequence[object],
) -> int:
    count = 0
    for raw in suggestions:
        item = as_object(raw, label="suggestion")
        for raw_candidate in as_list(item.get("candidates") or [], label="candidates"):
            if not isinstance(raw_candidate, dict):
                continue
            candidate = as_object(raw_candidate, label="candidate")
            obligation_ref = str(item.get("obligationRef") or "")
            contract_ref = str(candidate.get("contractRef") or "")
            if not obligation_ref or not contract_ref:
                continue
            row = session.execute(
                select(StructureLinkSuggestionModel).where(
                    StructureLinkSuggestionModel.project_id == project_id,
                    StructureLinkSuggestionModel.obligation_ref == obligation_ref,
                    StructureLinkSuggestionModel.contract_ref == contract_ref,
                )
            ).scalar_one_or_none()
            now = datetime.now(UTC)
            reasons = list(as_list(candidate.get("reasons") or [], label="reasons"))
            if row is None:
                session.add(
                    StructureLinkSuggestionModel(
                        project_id=project_id,
                        obligation_ref=obligation_ref,
                        contract_ref=contract_ref,
                        score=as_int(candidate.get("score"), label="score"),
                        confidence=str(candidate.get("confidence") or "heuristic"),
                        reasons_json=reasons,
                        state="pending",
                        created=now,
                        updated=now,
                    )
                )
            elif row.state == "pending":
                row.score = as_int(candidate.get("score"), label="score")
                row.confidence = str(candidate.get("confidence") or "heuristic")
                row.reasons_json = reasons
                row.updated = now
            count += 1
    session.flush()
    return count


def confirm_obligation_link(
    session: Session,
    project_id: int,
    *,
    obligation_ref: str,
    contract_ref: str,
    author: str,
) -> dict[str, object]:
    now = datetime.now(UTC)
    suggestion = session.execute(
        select(StructureLinkSuggestionModel).where(
            StructureLinkSuggestionModel.project_id == project_id,
            StructureLinkSuggestionModel.obligation_ref == obligation_ref,
            StructureLinkSuggestionModel.contract_ref == contract_ref,
        )
    ).scalar_one_or_none()
    if suggestion is not None:
        suggestion.state = "accepted"
        suggestion.updated = now
    claim_id = f"claim:{obligation_ref}:{contract_ref}"[:128]
    existing = session.execute(
        select(DocCodeClaimModel).where(
            DocCodeClaimModel.project_id == project_id,
            DocCodeClaimModel.claim_id == claim_id,
        )
    ).scalar_one_or_none()
    content_hash = sha256_text(f"{obligation_ref}|{contract_ref}")
    if existing is None:
        existing = DocCodeClaimModel(
            project_id=project_id,
            claim_id=claim_id,
            kind="exports",
            status="confirmed",
            content_hash=content_hash,
            subject_ref=contract_ref,
            expected_json={"obligationRef": obligation_ref},
            provenance=author,
            created=now,
            updated=now,
        )
        session.add(existing)
    else:
        existing.status = "confirmed"
        existing.subject_ref = contract_ref
        existing.updated = now
    session.flush()
    return {"claimId": claim_id, "status": "confirmed", "contractRef": contract_ref}


def reconcile_findings(
    session: Session,
    project_id: int,
    drift: dict[str, object],
    *,
    snapshot_id: int,
    temporal_alignment: str,
    truncated: bool,
) -> list[dict[str, object]]:
    incoming = [
        as_object(item, label="finding")
        for item in as_list(drift.get("findings"), label="findings")
    ]
    incoming_by = {str(item["fingerprint"]): item for item in incoming}
    existing_rows = list(
        session.execute(
            select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
        ).scalars()
    )
    existing_by = {row.fingerprint: row for row in existing_rows}
    now = datetime.now(UTC)
    can_close = temporal_alignment == "aligned" and not truncated
    out: list[dict[str, object]] = []

    for item in incoming:
        fp = str(item["fingerprint"])
        row = existing_by.get(fp)
        if row is None:
            row = StructureFindingModel(
                project_id=project_id,
                fingerprint=fp,
                rule_id=str(item["ruleId"]),
                status="open",
                priority=str(item["priority"]),
                subject_refs_json=as_list(item["subjectRefs"], label="subjectRefs"),
                summary=str(item["summary"]),
                remediation_target=str(item["remediationTarget"]),
                evidence_json=as_list(item["evidence"], label="evidence"),
                missing_evidence_json=as_list(item["missingEvidence"], label="missingEvidence"),
                first_seen_snapshot_id=snapshot_id,
                last_seen_snapshot_id=snapshot_id,
                created=now,
                updated=now,
            )
            session.add(row)
        else:
            row.summary = str(item["summary"])
            row.priority = str(item["priority"])
            row.subject_refs_json = as_list(item["subjectRefs"], label="subjectRefs")
            row.evidence_json = as_list(item["evidence"], label="evidence")
            row.missing_evidence_json = as_list(item["missingEvidence"], label="missingEvidence")
            row.last_seen_snapshot_id = snapshot_id
            row.updated = now
            if row.status in {"resolved", "superseded"}:
                row.status = "open"
                row.resolved_by_snapshot_id = None
        out.append(_row_to_finding(row))

    for row in existing_rows:
        if row.fingerprint in incoming_by:
            continue
        row.last_seen_snapshot_id = snapshot_id
        row.updated = now
        if row.status == "superseded":
            out.append(_row_to_finding(row))
            continue
        if not can_close:
            row.status = "pending_verify"
        elif row.rule_id == "docs.obligation_unlinked":
            row.status = "superseded"
        elif row.status == "pending_verify":
            row.status = "resolved"
            row.resolved_by_snapshot_id = snapshot_id
        else:
            row.status = "pending_verify"
        out.append(_row_to_finding(row))
    session.flush()
    out.sort(key=lambda item: str(item["fingerprint"]))
    return out


def apply_waivers(
    session: Session,
    project_id: int,
    findings: Sequence[object],
    *,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    current = now or datetime.now(UTC)
    waivers = list(
        session.execute(
            select(StructureWaiverModel).where(StructureWaiverModel.project_id == project_id)
        ).scalars()
    )
    active = {
        row.finding_fingerprint: row
        for row in waivers
        if row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) > current
    }
    out: list[dict[str, object]] = []
    for raw in findings:
        item = as_object(raw, label="finding")
        waiver = active.get(str(item.get("fingerprint") or ""))
        enriched = dict(item)
        if waiver is not None:
            enriched["suppressed"] = True
            enriched["waiver"] = {
                "owner": waiver.owner,
                "reason": waiver.reason,
                "expiresAt": waiver.expires_at.isoformat(),
            }
        else:
            enriched["suppressed"] = False
        out.append(enriched)
    return out


def upsert_waiver(
    session: Session,
    project_id: int,
    *,
    finding_fingerprint: str,
    owner: str,
    reason: str,
    expires_at: datetime,
    scope: str = "",
) -> dict[str, object]:
    if not owner.strip() or not reason.strip():
        raise ValueError("waiver owner and reason are required")
    now = datetime.now(UTC)
    row = session.execute(
        select(StructureWaiverModel).where(
            StructureWaiverModel.project_id == project_id,
            StructureWaiverModel.finding_fingerprint == finding_fingerprint,
        )
    ).scalar_one_or_none()
    if row is None:
        row = StructureWaiverModel(
            project_id=project_id,
            finding_fingerprint=finding_fingerprint,
            owner=owner,
            reason=reason,
            scope=scope,
            expires_at=expires_at,
            created=now,
        )
        session.add(row)
    else:
        row.owner = owner
        row.reason = reason
        row.scope = scope
        row.expires_at = expires_at
    session.flush()
    return {
        "findingFingerprint": finding_fingerprint,
        "owner": owner,
        "reason": reason,
        "expiresAt": expires_at.isoformat(),
        "scope": scope,
    }


def triage_findings(
    findings: Sequence[object],
    *,
    limit: int = 20,
) -> list[dict[str, object]]:
    rank = {"high": 3, "medium": 2, "info": 1, "low": 0}
    visible: list[dict[str, object]] = []
    for raw in findings:
        item = as_object(raw, label="finding")
        if item.get("suppressed"):
            continue
        if str(item.get("status") or "") in {"resolved", "superseded"}:
            continue
        missing = as_list(item.get("missingEvidence") or [], label="missing")
        visible.append(
            {
                **item,
                "explain": [
                    str(item.get("summary") or ""),
                    f"Missing: {', '.join(str(x) for x in missing)}"
                    if missing
                    else "Evidence threshold satisfied.",
                ],
                "nextAction": item.get("remediationTarget"),
            }
        )
    visible.sort(
        key=lambda item: (
            -rank.get(str(item.get("priority") or ""), 0),
            str(item.get("ruleId") or ""),
            str(item.get("fingerprint") or ""),
        )
    )
    return visible[:limit]


def _row_to_finding(row: StructureFindingModel) -> dict[str, object]:
    return {
        "id": row.fingerprint,
        "fingerprint": row.fingerprint,
        "ruleId": row.rule_id,
        "priority": row.priority,
        "status": row.status,
        "subjectRefs": list(row.subject_refs_json or []),
        "remediationTarget": row.remediation_target,
        "summary": row.summary,
        "evidence": list(row.evidence_json or []),
        "missingEvidence": list(row.missing_evidence_json or []),
        "firstSeenSnapshot": row.first_seen_snapshot_id,
        "lastSeenSnapshot": row.last_seen_snapshot_id,
        "resolvedBySnapshot": row.resolved_by_snapshot_id,
        "promotedTaskId": row.promoted_task_id,
    }
