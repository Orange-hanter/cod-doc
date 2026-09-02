"""REST: obligations export and structure snapshot queries."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cod_doc.api.deps import get_project_db
from cod_doc.services.structure_protocol import StructureProtocolError, as_object

router = APIRouter(prefix="/api/projects/{slug}", tags=["structure"])


class SnapshotIngest(BaseModel):
    facts: dict[str, object]
    assessment: dict[str, object] | None = None
    trust_tier: str = Field(default="untrusted")


@router.get("/obligations")
def get_obligations(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    head_sha: str = "unknown",
) -> dict[str, object]:
    from cod_doc.services.structure_obligations import export_obligations

    session, project_id = db
    return export_obligations(session, project_id, project_slug=slug, head_sha=head_sha)


@router.post("/structure/snapshots", status_code=201)
def post_snapshot(
    slug: str,
    body: SnapshotIngest,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
) -> dict[str, object]:
    from cod_doc.services.structure_service import ingest_structure

    session, project_id = db
    try:
        result = ingest_structure(
            session,
            project_id,
            facts=as_object(body.facts, label="facts"),
            assessment=as_object(body.assessment, label="assessment") if body.assessment else None,
            trust_tier=body.trust_tier,
            project_slug=slug,
            actor="rest",
        )
    except StructureProtocolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return result


@router.get("/structure/latest")
def get_latest(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    branch_ref: str | None = None,
    head_sha: str | None = None,
    pr_number: int | None = Query(default=None),
) -> dict[str, object]:
    from cod_doc.services import structure_service

    session, project_id = db
    try:
        row = structure_service.get_latest(
            session, project_id, head_sha=head_sha, branch_ref=branch_ref, pr_number=pr_number
        )
    except StructureProtocolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return structure_service._header(row)


@router.get("/structure/context")
def get_context(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    head_sha: str | None = None,
    snapshot_fingerprint: str | None = None,
    scope: str | None = None,
    budget_tokens: int = 4000,
) -> dict[str, object]:
    from cod_doc.services.structure_context import build_structure_context
    from cod_doc.services.structure_protocol import StructureProtocolError as SPE

    session, project_id = db
    try:
        return build_structure_context(
            session,
            project_id,
            project_slug=slug,
            head_sha=head_sha,
            snapshot_fingerprint=snapshot_fingerprint,
            scope_refs=[item for item in (scope or "").split(",") if item],
            budget_tokens=budget_tokens,
        )
    except SPE as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/structure/drift")
def get_drift(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    head_sha: str | None = None,
    snapshot_fingerprint: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    from sqlalchemy import select

    from cod_doc.infra.models.structure import StructureFindingModel
    from cod_doc.services import structure_service
    from cod_doc.services.structure_drift import apply_waivers

    session, project_id = db
    try:
        snapshot = structure_service.require_pinned_snapshot(
            session, project_id, head_sha=head_sha, snapshot_fingerprint=snapshot_fingerprint
        )
    except StructureProtocolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows = list(
        session.execute(
            select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
        ).scalars()
    )
    findings = apply_waivers(
        session,
        project_id,
        [
            {
                "fingerprint": row.fingerprint,
                "ruleId": row.rule_id,
                "status": row.status,
                "summary": row.summary,
                "priority": row.priority,
                "subjectRefs": list(row.subject_refs_json or []),
                "missingEvidence": list(row.missing_evidence_json or []),
                "remediationTarget": row.remediation_target,
            }
            for row in rows
        ],
    )
    start = int(cursor or "0")
    return {
        "kind": "structure_drift",
        "snapshotFingerprint": snapshot.fingerprint,
        "items": findings[start : start + limit],
        "nextCursor": str(start + limit) if start + limit < len(findings) else None,
    }


@router.get("/structure/scenarios")
def get_scenarios(
    slug: str,
    db: Annotated[tuple[Session, int], Depends(get_project_db)],
    head_sha: str | None = None,
    snapshot_fingerprint: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    from sqlalchemy import select

    from cod_doc.infra.models.structure import StructureAssessmentModel
    from cod_doc.services import structure_service
    from cod_doc.services.structure_protocol import as_list, as_object

    session, project_id = db
    try:
        snapshot = structure_service.require_pinned_snapshot(
            session, project_id, head_sha=head_sha, snapshot_fingerprint=snapshot_fingerprint
        )
    except StructureProtocolError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = session.execute(
        select(StructureAssessmentModel)
        .where(StructureAssessmentModel.snapshot_id == snapshot.row_id)
        .order_by(StructureAssessmentModel.created.desc())
        .limit(1)
    ).scalar_one_or_none()
    items: list[object] = []
    if row is not None:
        payload = structure_service.get_assessment_payload(row)
        assessments = as_object(payload.get("assessments") or {}, label="assessments")
        items = as_list(assessments.get("contractScenarios") or [], label="scenarios")
    start = int(cursor or "0")
    return {
        "items": items[start : start + limit],
        "nextCursor": str(start + limit) if start + limit < len(items) else None,
    }
