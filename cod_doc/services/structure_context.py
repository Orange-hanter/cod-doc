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
_TRUNCATED_ENTITIES = 40
_TRUNCATED_CONTRACTS = 20
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
    gaps = _gap_findings(
        session,
        project_id,
        selected,
        {str(item["id"]) for item in selected_contracts if item.get("id")},
    )
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
            # Партиция снапшота: без неё вызов по headSha на разбитом
            # репозитории получает произвольную часть графа без признака,
            # что остальное отсутствует.
            "scope": snapshot.scope,
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
    taken: set[int] = set()
    frontier = set(seeds)
    for _ in range(depth):
        nxt: set[str] = set()
        exhausted = False
        for index, edge in enumerate(edges):
            if len(selected_edges) >= MAX_CONTEXT_EDGES:
                exhausted = True
                break
            if index in taken:
                continue
            # Раскрываем только текущий фронт. Проверка против растущего
            # ``selected`` пропускала бы цепочку a→b→c→d целиком за один round,
            # и радиус выборки зависел бы от порядка строк в БД, а не от depth.
            if edge.from_ref not in frontier and edge.to_ref not in frontier:
                continue
            taken.add(index)
            selected_edges.append(edge)
            for ref in (edge.from_ref, edge.to_ref):
                if ref not in selected:
                    selected.add(ref)
                    nxt.add(ref)
        frontier = nxt
        if exhausted or not frontier:
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


_CODE_REF_PREFIXES = ("entity:", "contract:")


def _gap_is_relevant(
    row: StructureFindingModel,
    selected: set[str],
    contract_refs: set[str],
) -> bool:
    refs = {str(item) for item in (row.subject_refs_json or [])}
    if not refs or refs & selected or refs & contract_refs:
        return True
    # Ни один субъект не адресует код (это id обязательства, claim'а или
    # `structure:graph`) — находка общерепозиторная и относится к любому срезу.
    return not any(ref.startswith(_CODE_REF_PREFIXES) for ref in refs)


def _gap_findings(
    session: Session,
    project_id: int,
    selected: set[str],
    contract_refs: set[str],
) -> list[dict[str, object]]:
    """Активные находки, относящиеся к выбранному срезу.

    ``selected`` содержит только observedId сущностей и концы рёбер, а субъекты
    находок — это id обязательств, claim'ов и контрактов. Сопоставление лишь с
    ``selected`` почти никогда не срабатывало, и ``gaps`` приходил пустым;
    запасная ветка ``or not selected`` была недостижима, потому что seeds по
    умолчанию берутся из первых сущностей. Поэтому сверяем и с контрактами
    среза, а находки, чьи субъекты вообще не принадлежат этому снапшоту
    (например ``structure:graph``), считаем общерепозиторными и оставляем.
    """
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
            "scope": row.scope,
            "subjectRefs": list(row.subject_refs_json or []),
            "summary": row.summary,
            "missingEvidence": list(row.missing_evidence_json or []),
            "remediationTarget": row.remediation_target,
        }
        for row in finding_rows
        if _gap_is_relevant(row, selected, contract_refs)
    ]
    waived = apply_waivers(session, project_id, raw_findings)
    return triage_findings(waived, limit=MAX_CONTEXT_GAPS)


_SHRINKABLE = ("entities", "contracts", "dependencies", "obligations", "gaps")


def _shrink_largest(payload: dict[str, object]) -> bool:
    """Укоротить самую длинную секцию вдвое. False — резать больше нечего."""
    longest = ""
    length = 1
    for key in _SHRINKABLE:
        section = payload.get(key)
        if isinstance(section, list) and len(section) > length:
            longest, length = key, len(section)
    if not longest:
        return False
    section = payload[longest]
    assert isinstance(section, list)
    payload[longest] = section[: length // 2]
    return True


def _apply_budget(payload: dict[str, object], *, budget_tokens: int) -> dict[str, object]:
    encoded = canonical_json(payload)
    truncated = (
        len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES
        or len(encoded) // _TOKEN_DIVISOR > budget_tokens
    )
    if truncated:
        for key, limit in (
            ("dependencies", _TRUNCATED_DEPS),
            ("obligations", _TRUNCATED_OBLIGATIONS),
            ("gaps", _TRUNCATED_GAPS),
            ("entities", _TRUNCATED_ENTITIES),
            ("contracts", _TRUNCATED_CONTRACTS),
        ):
            section = payload.get(key)
            if isinstance(section, list):
                payload[key] = section[:limit]
        payload["truncated"] = True
        encoded = canonical_json(payload)
        # Обрезка секций — оценка, а не гарантия: если и после неё payload не
        # влезает в бюджет, режем самые крупные списки до последнего элемента,
        # чтобы контракт «ответ помещается в бюджет» оставался верным.
        while (
            len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES
            or len(encoded) // _TOKEN_DIVISOR > budget_tokens
        ) and _shrink_largest(payload):
            encoded = canonical_json(payload)
    payload["tokenEstimate"] = max(1, len(encoded) // _TOKEN_DIVISOR)
    payload["bytes"] = len(encoded.encode("utf-8"))
    return payload


def _names(entities: Sequence[Mapping[str, object]]) -> list[str]:
    return [str(item.get("name") or "").lower() for item in entities if item.get("name")]
