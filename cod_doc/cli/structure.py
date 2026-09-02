"""CLI: ``cod-doc structure`` — snapshots, drift, triage, waivers, replay."""

from __future__ import annotations

import json as _json
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config

console = Console()


def _console() -> Console:
    return console


def _session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    entry = cfg.get_project(project_name)
    if not entry:
        _console().print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    url = resolve_db_url(Path(entry.path))
    return make_session_factory(make_engine(url))


def _project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        _console().print(f"[red]Project '{project_name}' not in DB.[/red]")
        sys.exit(1)
    return proj.row_id


def _dump(payload: object, as_json: bool) -> None:
    if as_json:
        click.echo(_json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    _console().print(payload)


@click.group()
def structure() -> None:
    """Code structure snapshots, drift, triage and waivers (not projection drift)."""


@structure.command("latest")
@click.option("--project", "-p", required=True)
@click.option("--branch", "branch_ref", default=None)
@click.option("--pr", "pr_number", type=int, default=None)
@click.option("--head-sha", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def structure_latest(
    ctx: click.Context,
    project: str,
    branch_ref: str | None,
    pr_number: int | None,
    head_sha: str | None,
    as_json: bool,
) -> None:
    """Show current snapshot for main, a PR, or an explicit SHA."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import structure_service

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg)) as session:
        project_id = _project_id(session, project)
        row = structure_service.get_latest(
            session, project_id, head_sha=head_sha, branch_ref=branch_ref, pr_number=pr_number
        )
        if row is None:
            _console().print("[yellow]No snapshot matches the selector.[/yellow]")
            sys.exit(2)
        payload = structure_service._header(row)
    _dump(payload, as_json)


@structure.command("get")
@click.option("--project", "-p", required=True)
@click.option("--fingerprint", required=True)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_get(ctx: click.Context, project: str, fingerprint: str, as_json: bool) -> None:
    """Return a stored facts payload by fingerprint."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import structure_service

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        row = structure_service.get_snapshot_by_fingerprint(session, project_id, fingerprint)
        if row is None:
            _console().print("[red]Snapshot not found.[/red]")
            sys.exit(2)
        payload = {
            "header": structure_service._header(row),
            "facts": structure_service.get_snapshot_payload(row),
        }
    _dump(payload, as_json)


@structure.command("entities")
@click.option("--project", "-p", required=True)
@click.option("--head-sha", default=None)
@click.option("--fingerprint", "snapshot_fingerprint", default=None)
@click.option("--cursor", default=None)
@click.option("--limit", default=50, type=int)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_entities(
    ctx: click.Context,
    project: str,
    head_sha: str | None,
    snapshot_fingerprint: str | None,
    cursor: str | None,
    limit: int,
    as_json: bool,
) -> None:
    from cod_doc.infra.db import transactional
    from cod_doc.services import structure_service

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        snapshot = structure_service.require_pinned_snapshot(
            session, project_id, head_sha=head_sha, snapshot_fingerprint=snapshot_fingerprint
        )
        payload = structure_service.list_entities(
            session, snapshot.row_id, cursor=cursor, limit=limit
        )
    _dump(payload, as_json)


@structure.command("contracts")
@click.option("--project", "-p", required=True)
@click.option("--head-sha", default=None)
@click.option("--fingerprint", "snapshot_fingerprint", default=None)
@click.option("--cursor", default=None)
@click.option("--limit", default=50, type=int)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_contracts(
    ctx: click.Context,
    project: str,
    head_sha: str | None,
    snapshot_fingerprint: str | None,
    cursor: str | None,
    limit: int,
    as_json: bool,
) -> None:
    from cod_doc.infra.db import transactional
    from cod_doc.services import structure_service

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        snapshot = structure_service.require_pinned_snapshot(
            session, project_id, head_sha=head_sha, snapshot_fingerprint=snapshot_fingerprint
        )
        payload = structure_service.list_contracts(
            session, snapshot.row_id, cursor=cursor, limit=limit
        )
    _dump(payload, as_json)


@structure.command("drift")
@click.option("--project", "-p", required=True)
@click.option("--head-sha", default=None)
@click.option("--fingerprint", "snapshot_fingerprint", default=None)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_drift_cmd(
    ctx: click.Context,
    project: str,
    head_sha: str | None,
    snapshot_fingerprint: str | None,
    as_json: bool,
) -> None:
    """Structure/docs/scenario drift. Distinct from ``doc drift`` projection check."""
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models.structure import StructureFindingModel
    from cod_doc.services import structure_service
    from cod_doc.services.structure_drift import apply_waivers
    from cod_doc.services.structure_service import _header

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        snapshot = structure_service.require_pinned_snapshot(
            session, project_id, head_sha=head_sha, snapshot_fingerprint=snapshot_fingerprint
        )
        rows = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        findings = [
            {
                "fingerprint": row.fingerprint,
                "ruleId": row.rule_id,
                "status": row.status,
                "priority": row.priority,
                "summary": row.summary,
                "subjectRefs": list(row.subject_refs_json or []),
                "missingEvidence": list(row.missing_evidence_json or []),
                "remediationTarget": row.remediation_target,
            }
            for row in rows
        ]
        payload = {
            "kind": "structure_drift",
            "snapshot": _header(snapshot),
            "findings": apply_waivers(session, project_id, findings),
        }
    _dump(payload, as_json)


@structure.command("triage")
@click.option("--project", "-p", required=True)
@click.option("--limit", default=20, type=int)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_triage(ctx: click.Context, project: str, limit: int, as_json: bool) -> None:
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models.structure import StructureFindingModel
    from cod_doc.services.structure_drift import apply_waivers, triage_findings

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        rows = list(
            session.execute(
                select(StructureFindingModel).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
        findings = [
            {
                "fingerprint": row.fingerprint,
                "ruleId": row.rule_id,
                "status": row.status,
                "priority": row.priority,
                "summary": row.summary,
                "subjectRefs": list(row.subject_refs_json or []),
                "missingEvidence": list(row.missing_evidence_json or []),
                "remediationTarget": row.remediation_target,
            }
            for row in rows
        ]
        payload = triage_findings(apply_waivers(session, project_id, findings), limit=limit)
    _dump(payload, as_json)


@structure.command("scenarios")
@click.option("--project", "-p", required=True)
@click.option("--head-sha", default=None)
@click.option("--fingerprint", "snapshot_fingerprint", default=None)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_scenarios(
    ctx: click.Context,
    project: str,
    head_sha: str | None,
    snapshot_fingerprint: str | None,
    as_json: bool,
) -> None:
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models.structure import StructureAssessmentModel
    from cod_doc.services import structure_service
    from cod_doc.services.structure_protocol import as_list, as_object

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        snapshot = structure_service.require_pinned_snapshot(
            session, project_id, head_sha=head_sha, snapshot_fingerprint=snapshot_fingerprint
        )
        row = session.execute(
            select(StructureAssessmentModel)
            .where(StructureAssessmentModel.snapshot_id == snapshot.row_id)
            .order_by(StructureAssessmentModel.created.desc())
            .limit(1)
        ).scalar_one_or_none()
        scenarios: list[object] = []
        if row is not None:
            payload_obj = structure_service.get_assessment_payload(row)
            assessments = as_object(payload_obj.get("assessments") or {}, label="assessments")
            scenarios = as_list(assessments.get("contractScenarios") or [], label="scenarios")
    _dump({"items": scenarios, "snapshotFingerprint": snapshot.fingerprint}, as_json)


@structure.command("diff")
@click.option("--project", "-p", required=True)
@click.option("--left", "left_id", required=True, type=int)
@click.option("--right", "right_id", required=True, type=int)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_diff(
    ctx: click.Context, project: str, left_id: int, right_id: int, as_json: bool
) -> None:
    from cod_doc.infra.db import transactional
    from cod_doc.services import structure_service

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        payload = structure_service.diff_snapshots(session, project_id, left_id, right_id)
    _dump(payload, as_json)


@structure.command("link-suggest")
@click.option("--project", "-p", required=True)
@click.option("--head-sha", default=None)
@click.option("--fingerprint", "snapshot_fingerprint", default=None)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_link_suggest(
    ctx: click.Context,
    project: str,
    head_sha: str | None,
    snapshot_fingerprint: str | None,
    as_json: bool,
) -> None:
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models.structure import StructureLinkSuggestionModel
    from cod_doc.services import structure_service
    from cod_doc.services.structure_drift import persist_link_suggestions, suggest_obligation_links
    from cod_doc.services.structure_obligations import export_obligations
    from cod_doc.services.structure_protocol import as_list

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg)) as session:
        project_id = _project_id(session, project)
        snapshot = structure_service.require_pinned_snapshot(
            session, project_id, head_sha=head_sha, snapshot_fingerprint=snapshot_fingerprint
        )
        facts = structure_service.get_snapshot_payload(snapshot)
        obligations = export_obligations(
            session, project_id, project_slug=project, head_sha=snapshot.head_sha
        )
        suggestions = suggest_obligation_links(
            as_list(obligations.get("obligations"), label="obligations"), facts
        )
        persist_link_suggestions(session, project_id, suggestions)
        stored = list(
            session.execute(
                select(StructureLinkSuggestionModel).where(
                    StructureLinkSuggestionModel.project_id == project_id,
                    StructureLinkSuggestionModel.state == "pending",
                )
            ).scalars()
        )
        payload = [
            {
                "obligationRef": row.obligation_ref,
                "contractRef": row.contract_ref,
                "score": row.score,
                "confidence": row.confidence,
                "state": row.state,
            }
            for row in stored
        ]
    _dump(payload, as_json)


@structure.command("link-confirm")
@click.option("--project", "-p", required=True)
@click.option("--obligation", required=True)
@click.option("--contract", required=True)
@click.option("--author", default="human")
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_link_confirm(
    ctx: click.Context,
    project: str,
    obligation: str,
    contract: str,
    author: str,
    as_json: bool,
) -> None:
    from cod_doc.infra.db import transactional
    from cod_doc.services.structure_drift import confirm_obligation_link

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg)) as session:
        project_id = _project_id(session, project)
        payload = confirm_obligation_link(
            session,
            project_id,
            obligation_ref=obligation,
            contract_ref=contract,
            author=author,
        )
    _dump(payload, as_json)


@structure.command("waive")
@click.option("--project", "-p", required=True)
@click.option("--fingerprint", required=True)
@click.option("--owner", required=True)
@click.option("--reason", required=True)
@click.option("--expires-at", required=True, help="ISO-8601 expiry")
@click.option("--scope", default="")
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_waive(
    ctx: click.Context,
    project: str,
    fingerprint: str,
    owner: str,
    reason: str,
    expires_at: str,
    scope: str,
    as_json: bool,
) -> None:
    from cod_doc.infra.db import transactional
    from cod_doc.services.structure_drift import upsert_waiver

    cfg: Config = ctx.obj["config"]
    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    with transactional(_session(project, cfg)) as session:
        project_id = _project_id(session, project)
        payload = upsert_waiver(
            session,
            project_id,
            finding_fingerprint=fingerprint,
            owner=owner,
            reason=reason,
            expires_at=expiry,
            scope=scope,
        )
    _dump(payload, as_json)


@structure.command("waivers")
@click.option("--project", "-p", required=True)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_waivers(ctx: click.Context, project: str, as_json: bool) -> None:
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models.structure import StructureWaiverModel

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        project_id = _project_id(session, project)
        rows = list(
            session.execute(
                select(StructureWaiverModel).where(StructureWaiverModel.project_id == project_id)
            ).scalars()
        )
        payload = [
            {
                "fingerprint": row.finding_fingerprint,
                "owner": row.owner,
                "reason": row.reason,
                "expiresAt": row.expires_at.isoformat(),
                "scope": row.scope,
            }
            for row in rows
        ]
    _dump(payload, as_json)


@structure.command("replay")
@click.option("--project", "-p", required=True)
@click.option("--snapshot-id", required=True, type=int)
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_replay(ctx: click.Context, project: str, snapshot_id: int, as_json: bool) -> None:
    from cod_doc.infra.db import transactional
    from cod_doc.services import structure_service

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg)) as session:
        project_id = _project_id(session, project)
        payload = structure_service.replay_snapshot(session, project_id, snapshot_id)
    _dump(payload, as_json)


@structure.command("promote")
@click.option("--project", "-p", required=True)
@click.option("--fingerprint", required=True)
@click.option("--plan-id", required=True)
@click.option("--section-id", required=True)
@click.option("--author", default="human")
@click.option("--json", "as_json", is_flag=True, default=True)
@click.pass_context
def structure_promote(
    ctx: click.Context,
    project: str,
    fingerprint: str,
    plan_id: str,
    section_id: str,
    author: str,
    as_json: bool,
) -> None:
    """Promote a structure finding into a task. Does not ingest ai_review findings."""
    from sqlalchemy import select

    from cod_doc.domain.entities import Priority, TaskType
    from cod_doc.infra.db import transactional
    from cod_doc.infra.models.plans import PlanModel, PlanSectionModel
    from cod_doc.infra.models.structure import StructureFindingModel
    from cod_doc.services import task_service

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg)) as session:
        project_id = _project_id(session, project)
        row = session.execute(
            select(StructureFindingModel).where(
                StructureFindingModel.project_id == project_id,
                StructureFindingModel.fingerprint == fingerprint,
            )
        ).scalar_one_or_none()
        if row is None:
            _console().print("[red]Finding not found.[/red]")
            sys.exit(2)
        plan = session.execute(
            select(PlanModel).where(PlanModel.scope == plan_id, PlanModel.project_id == project_id)
        ).scalar_one_or_none()
        if plan is None or plan.row_id is None:
            _console().print(f"[red]Plan scope '{plan_id}' not found.[/red]")
            sys.exit(2)
        section = session.execute(
            select(PlanSectionModel).where(
                PlanSectionModel.plan_id == plan.row_id,
                PlanSectionModel.letter == section_id,
            )
        ).scalar_one_or_none()
        if section is None or section.row_id is None:
            _console().print(f"[red]Plan section '{section_id}' not found.[/red]")
            sys.exit(2)
        task = task_service.create(
            session,
            project_id=project_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title=f"[structure] {row.summary[:80]}",
            type=TaskType.TEST if row.remediation_target == "test" else TaskType.DOCS,
            priority=Priority.HIGH if row.priority == "high" else Priority.MEDIUM,
            author=author,
            id_prefix="STR",
            description=row.summary,
            affected_files=[str(x) for x in row.subject_refs_json or [] if "/" in str(x)],
        )
        row.promoted_task_id = task.task_id
        row.status = "in_progress"
        payload = {"taskId": task.task_id, "fingerprint": fingerprint}
    _dump(payload, as_json)
