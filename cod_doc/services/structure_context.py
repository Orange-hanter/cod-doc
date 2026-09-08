"""Pinned structure_context slices for plans and fixes.

Implicit latest is forbidden. Callers must pass headSha or snapshotFingerprint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models.structure import (
    CodeContractModel,
    CodeEdgeModel,
    CodeEntityModel,
    StructureAssessmentModel,
    StructureFindingModel,
)
from cod_doc.services.structure_drift import apply_waivers, triage_findings
from cod_doc.services.structure_obligations import export_obligations
from cod_doc.services.structure_protocol import (
    MAX_CONTEXT_BYTES,
    MAX_CONTEXT_EDGES,
    MAX_CONTEXT_GAPS,
    MAX_CONTEXT_OBLIGATIONS,
    MAX_CONTEXT_SEEDS,
    as_list,
    as_object,
    canonical_json,
)
from cod_doc.services.structure_service import get_assessment_payload, require_pinned_snapshot

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session

_MAX_DEPENDENCY_DEPTH = 3
_TOKEN_DIVISOR = 4
_TRUNCATED_DEPS = 10
_TRUNCATED_OBLIGATIONS = 5
_TRUNCATED_GAPS = 5
_SUGGESTED_FILES = 20
_ACTIVE_FINDING_STATUSES = ("open", "in_progress", "pending_verify")


def build_structure_context(
    session: Session,
    project_id: int,
    *,
    project_slug: str,
    head_sha: str | None = None,
    snapshot_fingerprint: str | None = None,
    scope_refs: list[str] | None = None,
    dependency_depth: int = 1,
    budget_tokens: int = 4000,
) -> dict[str, object]:
    snapshot = require_pinned_snapshot(
        session,
        project_id,
        head_sha=head_sha,
        snapshot_fingerprint=snapshot_fingerprint,
    )
    entities, contracts, edges = _load_graph(session, snapshot.row_id)
    seeds = list(dict.fromkeys(scope_refs or []))[:MAX_CONTEXT_SEEDS]
    if not seeds:
        seeds = [row.observed_id for row in list(entities.values())[:MAX_CONTEXT_SEEDS]]
    depth = max(0, min(dependency_depth, _MAX_DEPENDENCY_DEPTH))
    selected, selected_edges = _walk_neighborhood(seeds, edges, depth)
    selected_entities = [
        {
            "observedId": row.observed_id,
            "kind": row.kind,
            "name": row.name,
            "path": row.path,
            "confidence": row.confidence,
        }
        for observed_id, row in entities.items()
        if observed_id in selected
    ]
    selected_contracts = [
        {
            "id": row.contract_id,
            "entityRef": row.entity_ref,
            "name": row.name,
            "kind": row.kind,
            "path": row.path,
        }
        for row in contracts
        if row.entity_ref in selected or row.contract_id in selected
    ][: MAX_CONTEXT_SEEDS * 2]
    selected_deps = [
        {
            "from": edge.from_ref,
            "to": edge.to_ref,
            "relation": edge.relation,
            "confidence": edge.confidence,
        }
        for edge in selected_edges[:MAX_CONTEXT_EDGES]
    ]

    assessment_row = session.execute(
        select(StructureAssessmentModel)
        .where(StructureAssessmentModel.snapshot_id == snapshot.row_id)
        .order_by(StructureAssessmentModel.created.desc())
        .limit(1)
    ).scalar_one_or_none()
    assessment_payload = get_assessment_payload(assessment_row) if assessment_row else None
    obligations = export_obligations(
        session, project_id, project_slug=project_slug, head_sha=snapshot.head_sha
    )
    relevant_obligations = _filter_obligations(obligations, selected, selected_entities)
    gaps = _gap_findings(session, project_id, selected)
    scenarios = _filter_scenarios(assessment_payload, selected, relevant_obligations)
    suggested_files = sorted({str(item["path"]) for item in selected_entities if item.get("path")})[
        :_SUGGESTED_FILES
    ]
    payload: dict[str, object] = {
        "pinned": {
            "headSha": snapshot.head_sha,
            "snapshotFingerprint": snapshot.fingerprint,
            "assessmentFingerprint": assessment_row.fingerprint if assessment_row else None,
            "obligationsRevision": obligations.get("revision"),
            "branchRef": snapshot.branch_ref,
            "trustTier": snapshot.trust_tier,
        },
        "temporalAlignment": (assessment_payload or {}).get("temporalAlignment") or "unknown",
        "entities": selected_entities,
        "contracts": selected_contracts,
        "dependencies": selected_deps,
        "obligations": relevant_obligations,
        "scenarios": scenarios,
        "gaps": gaps,
        "suggestedFiles": suggested_files,
        "truncated": False,
    }
    return _apply_budget(payload, budget_tokens=budget_tokens)


def _load_graph(
    session: Session, snapshot_id: int
) -> tuple[dict[str, CodeEntityModel], list[CodeContractModel], list[CodeEdgeModel]]:
    entities = {
        row.observed_id: row
        for row in session.execute(
            select(CodeEntityModel).where(CodeEntityModel.snapshot_id == snapshot_id)
        ).scalars()
    }
    contracts = list(
        session.execute(
            select(CodeContractModel).where(CodeContractModel.snapshot_id == snapshot_id)
        ).scalars()
    )
    edges = list(
        session.execute(
            select(CodeEdgeModel).where(CodeEdgeModel.snapshot_id == snapshot_id)
        ).scalars()
    )
    return entities, contracts, edges


def _walk_neighborhood(
    seeds: list[str],
    edges: list[CodeEdgeModel],
    depth: int,
) -> tuple[set[str], list[CodeEdgeModel]]:
    selected = set(seeds)
    selected_edges: list[CodeEdgeModel] = []
    frontier = list(seeds)
    for _ in range(depth):
        nxt: list[str] = []
        for edge in edges:
            if len(selected_edges) >= MAX_CONTEXT_EDGES:
                break
            if edge.from_ref not in selected and edge.to_ref not in selected:
                continue
            if edge in selected_edges:
                continue
            selected_edges.append(edge)
            if edge.from_ref not in selected:
                selected.add(edge.from_ref)
                nxt.append(edge.from_ref)
            if edge.to_ref not in selected:
                selected.add(edge.to_ref)
                nxt.append(edge.to_ref)
        frontier = nxt
        if not frontier:
            break
    return selected, selected_edges


def _filter_obligations(
    obligations: dict[str, object],
    selected: set[str],
    selected_entities: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    names = _names(selected_entities)
    relevant: list[dict[str, object]] = []
    for raw in as_list(obligations.get("obligations"), label="obligations"):
        item = as_object(raw, label="obligation")
        refs = {str(ref) for ref in as_list(item.get("contractRefs") or [], label="cr")}
        statement = str(item.get("statement") or "").lower()
        if refs & selected or any(name in statement for name in names):
            relevant.append(item)
        if len(relevant) >= MAX_CONTEXT_OBLIGATIONS:
            break
    return relevant


def _filter_scenarios(
    assessment_payload: dict[str, object] | None,
    selected: set[str],
    relevant_obligations: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not assessment_payload:
        return []
    obl_ids = {str(obl.get("id") or "") for obl in relevant_obligations}
    assessments = as_object(assessment_payload.get("assessments") or {}, label="assessments")
    scenarios: list[dict[str, object]] = []
    for raw in as_list(assessments.get("contractScenarios") or [], label="cs"):
        if not isinstance(raw, dict):
            continue
        item = as_object(raw, label="scenario")
        refs = {str(x) for x in as_list(item.get("contractRefs") or [], label="scr")}
        if refs & selected or str(item.get("obligationRef") or "") in obl_ids:
            scenarios.append(item)
    return scenarios[:MAX_CONTEXT_GAPS]


def _gap_findings(
    session: Session,
    project_id: int,
    selected: set[str],
) -> list[dict[str, object]]:
    finding_rows = list(
        session.execute(
            select(StructureFindingModel).where(
                StructureFindingModel.project_id == project_id,
                StructureFindingModel.status.in_(_ACTIVE_FINDING_STATUSES),
            )
        ).scalars()
    )
    raw_findings = [
        {
            "fingerprint": row.fingerprint,
            "ruleId": row.rule_id,
            "priority": row.priority,
            "status": row.status,
            "subjectRefs": list(row.subject_refs_json or []),
            "summary": row.summary,
            "missingEvidence": list(row.missing_evidence_json or []),
            "remediationTarget": row.remediation_target,
        }
        for row in finding_rows
        if selected.intersection({str(x) for x in (row.subject_refs_json or [])}) or not selected
    ]
    waived = apply_waivers(session, project_id, raw_findings)
    return triage_findings(waived, limit=MAX_CONTEXT_GAPS)


def _apply_budget(payload: dict[str, object], *, budget_tokens: int) -> dict[str, object]:
    encoded = canonical_json(payload)
    truncated = (
        len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES
        or len(encoded) // _TOKEN_DIVISOR > budget_tokens
    )
    if truncated:
        deps = payload.get("dependencies")
        obls = payload.get("obligations")
        gaps = payload.get("gaps")
        payload["dependencies"] = deps[:_TRUNCATED_DEPS] if isinstance(deps, list) else deps
        payload["obligations"] = obls[:_TRUNCATED_OBLIGATIONS] if isinstance(obls, list) else obls
        payload["gaps"] = gaps[:_TRUNCATED_GAPS] if isinstance(gaps, list) else gaps
        payload["truncated"] = True
        encoded = canonical_json(payload)
    payload["tokenEstimate"] = max(1, len(encoded) // _TOKEN_DIVISOR)
    payload["bytes"] = len(encoded.encode("utf-8"))
    return payload


def _names(entities: Sequence[Mapping[str, object]]) -> list[str]:
    return [str(item.get("name") or "").lower() for item in entities if item.get("name")]
