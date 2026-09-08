"""MCP tools: structure_get / context / drift / scenarios / diff.

Pinned SHA is mandatory for plan/fix context. Snapshots are not ai_review findings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.mcp.tools._db import require_project_id, session_factory
from cod_doc.services.structure_protocol import StructureProtocolError, parse_cursor

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _error(message: str) -> dict[str, object]:
    return {"ok": False, "error": {"code": "invalid_params", "message": message}}


def register(mcp: FastMCP) -> None:
    """Register structure.* tools on the given FastMCP instance."""

    @mcp.tool(name="structure_get")
    def structure_get(
        project: str,
        head_sha: str | None = None,
        snapshot_fingerprint: str | None = None,
    ) -> dict[str, object]:
        """Return a stored structure snapshot header + facts. Pin SHA or fingerprint."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import structure_service

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=False) as session:
                project_id = require_project_id(session, project)
                row = structure_service.require_pinned_snapshot(
                    session,
                    project_id,
                    head_sha=head_sha,
                    snapshot_fingerprint=snapshot_fingerprint,
                )
                return {
                    "ok": True,
                    "header": structure_service._header(row),
                    "facts": structure_service.get_snapshot_payload(row),
                }
        except StructureProtocolError as exc:
            return _error(str(exc))

    @mcp.tool(name="structure_context")
    def structure_context(
        project: str,
        head_sha: str | None = None,
        snapshot_fingerprint: str | None = None,
        scope_refs: list[str] | None = None,
        budget_tokens: int = 4000,
        dependency_depth: int = 1,
    ) -> dict[str, object]:
        """Pinned BFS structure slice for planning/fixing. No implicit latest."""
        from cod_doc.infra.db import transactional
        from cod_doc.services.structure_context import build_structure_context

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=False) as session:
                project_id = require_project_id(session, project)
                return build_structure_context(
                    session,
                    project_id,
                    project_slug=project,
                    head_sha=head_sha,
                    snapshot_fingerprint=snapshot_fingerprint,
                    scope_refs=scope_refs or [],
                    budget_tokens=budget_tokens,
                    dependency_depth=dependency_depth,
                )
        except StructureProtocolError as exc:
            return _error(str(exc))

    @mcp.tool(name="structure_drift")
    def structure_drift(
        project: str,
        head_sha: str | None = None,
        snapshot_fingerprint: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        """Structure/docs/scenario drift findings. Not DB↔Markdown projection drift."""
        from cod_doc.infra.db import transactional
        from cod_doc.infra.models.structure import StructureFindingModel
        from cod_doc.services import structure_service
        from cod_doc.services.structure_drift import apply_waivers

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=False) as session:
                project_id = require_project_id(session, project)
                snapshot = structure_service.require_pinned_snapshot(
                    session,
                    project_id,
                    head_sha=head_sha,
                    snapshot_fingerprint=snapshot_fingerprint,
                )
                rows = list(
                    session.execute(
                        select(StructureFindingModel).where(
                            StructureFindingModel.project_id == project_id
                        )
                    ).scalars()
                )
                start = parse_cursor(cursor)
                findings = apply_waivers(
                    session,
                    project_id,
                    [
                        {
                            "fingerprint": row.fingerprint,
                            "ruleId": row.rule_id,
                            "status": row.status,
                            "priority": row.priority,
                            "summary": row.summary,
                            "scope": row.scope,
                            "subjectRefs": list(row.subject_refs_json or []),
                            "missingEvidence": list(row.missing_evidence_json or []),
                            "remediationTarget": row.remediation_target,
                        }
                        for row in rows
                    ],
                )
                chunk = findings[start : start + limit]
                return {
                    "ok": True,
                    "kind": "structure_drift",
                    "snapshotFingerprint": snapshot.fingerprint,
                    "items": chunk,
                    "nextCursor": str(start + limit) if start + limit < len(findings) else None,
                }
        except StructureProtocolError as exc:
            return _error(str(exc))

    @mcp.tool(name="structure_scenarios")
    def structure_scenarios(
        project: str,
        head_sha: str | None = None,
        snapshot_fingerprint: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        """Contract scenario assessments from the pinned snapshot."""
        from sqlalchemy import select as sel

        from cod_doc.infra.db import transactional
        from cod_doc.infra.models.structure import StructureAssessmentModel
        from cod_doc.services import structure_service
        from cod_doc.services.structure_protocol import as_list, as_object

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=False) as session:
                project_id = require_project_id(session, project)
                snapshot = structure_service.require_pinned_snapshot(
                    session,
                    project_id,
                    head_sha=head_sha,
                    snapshot_fingerprint=snapshot_fingerprint,
                )
                row = session.execute(
                    sel(StructureAssessmentModel)
                    .where(StructureAssessmentModel.snapshot_id == snapshot.row_id)
                    .order_by(StructureAssessmentModel.created.desc())
                    .limit(1)
                ).scalar_one_or_none()
                items: list[object] = []
                if row is not None:
                    payload = structure_service.get_assessment_payload(row)
                    assessments = as_object(payload.get("assessments") or {}, label="assessments")
                    items = as_list(assessments.get("contractScenarios") or [], label="scenarios")
                start = parse_cursor(cursor)
                chunk = items[start : start + limit]
                return {
                    "ok": True,
                    "items": chunk,
                    "nextCursor": str(start + limit) if start + limit < len(items) else None,
                }
        except StructureProtocolError as exc:
            return _error(str(exc))

    @mcp.tool(name="structure_diff")
    def structure_diff(project: str, left_id: int, right_id: int) -> dict[str, object]:
        """Diff two stored snapshots: created/removed/moved/changed/unknown."""
        from cod_doc.infra.db import transactional
        from cod_doc.services import structure_service

        sf, _ = session_factory(project)
        try:
            with transactional(sf, commit=False) as session:
                project_id = require_project_id(session, project)
                return structure_service.diff_snapshots(session, project_id, left_id, right_id)
        except StructureProtocolError as exc:
            return _error(str(exc))
