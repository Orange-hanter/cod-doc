"""Scenario CRUD — create, read, update, retire.

Every mutation writes a revision and emits an activity event in one atomic
call (ADO-040). Steps and links are revisioned under their parent
``EntityKind.SCENARIO``, mirroring how acceptance criteria are revisioned
under their story.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import (
    EntityKind,
    Scenario,
    ScenarioKind,
    ScenarioProvenance,
    ScenarioStatus,
)
from cod_doc.infra.models import ScenarioModel, ScenarioStepModel
from cod_doc.infra.repositories import ScenarioRepository
from cod_doc.services import activity_service, validation

from ._internals import (
    _derive_group_key,
    _diff,
    _next_scenario_id,
    _require_scenario,
    _resolve_doc_anchor,
)
from ._types import ScenarioAlreadyExistsError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def create(
    session: Session,
    *,
    project_id: int,
    title: str,
    kind: ScenarioKind,
    preconditions: str,
    expected: str,
    author: str,
    steps: list[str] | None = None,
    scenario_id: str | None = None,
    group_key: str | None = None,
    doc_key: str | None = None,
    section_anchor: str | None = None,
    notes: str | None = None,
    status: ScenarioStatus = ScenarioStatus.DRAFT,
    provenance: ScenarioProvenance = ScenarioProvenance.MANUAL,
    reason: str | None = None,
) -> Scenario:
    """Persist a scenario (+ optional steps) and write its initial revision.

    ``group_key`` defaults to the anchored document's basename; one of
    ``group_key`` / ``doc_key`` must therefore be given.
    """
    resolved_group = group_key or (_derive_group_key(doc_key) if doc_key else None)
    if resolved_group is None:
        raise ValueError("either group_key or doc_key is required")
    validation.validate_scenario_group_key(resolved_group)
    validation.validate_scenario_kind(kind.value)
    validation.validate_scenario_status(status.value)
    validation.validate_scenario_body(
        preconditions=preconditions, expected=expected, steps=steps or []
    )

    repo = ScenarioRepository(session)
    sid = scenario_id or _next_scenario_id(session, project_id)
    validation.validate_scenario_id(sid)
    if repo.get_by_scenario_id(project_id, sid) is not None:
        raise ScenarioAlreadyExistsError(sid)

    document_id, doc_content_hash = _resolve_doc_anchor(
        session,
        project_id=project_id,
        doc_key=doc_key,
        section_anchor=section_anchor,
    )

    now = datetime.now(UTC)
    scenario = repo.add(
        Scenario(
            project_id=project_id,
            scenario_id=sid,
            title=title,
            kind=kind,
            group_key=resolved_group,
            preconditions=preconditions,
            expected=expected,
            author=author,
            status=status,
            provenance=provenance,
            position=repo.next_position(project_id, resolved_group),
            document_id=document_id,
            doc_key=doc_key,
            section_anchor=section_anchor,
            doc_content_hash=doc_content_hash,
            notes=notes,
            created=now,
            last_updated=now,
        )
    )
    assert scenario.row_id is not None

    for i, text in enumerate(steps or []):
        session.add(ScenarioStepModel(scenario_row_id=scenario.row_id, position=i, text=text))
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SCENARIO,
        entity_id=scenario.row_id,
        author=author,
        diff=_diff(
            "create",
            scenario_id=sid,
            kind=kind.value,
            group_key=resolved_group,
            step_count=len(steps or []),
        ),
        reason=reason or "create",
        activity_kind="scenario.created",
        activity_scope_kind="scenario",
        activity_scope_id=sid,
        activity_payload={
            "kind": kind.value,
            "group_key": resolved_group,
            "status": status.value,
            "step_count": len(steps or []),
        },
        activity_summary=f"Scenario {sid} created",
    )
    return scenario


def get(session: Session, project_id: int, scenario_id: str) -> Scenario | None:
    return ScenarioRepository(session).get_by_scenario_id(project_id, scenario_id)


def list_for_project(session: Session, project_id: int) -> list[Scenario]:
    return ScenarioRepository(session).list_for_project(project_id)


def list_for_group(session: Session, project_id: int, group_key: str) -> list[Scenario]:
    return ScenarioRepository(session).list_for_group(project_id, group_key)


def update(
    session: Session,
    *,
    project_id: int,
    scenario_id: str,
    author: str,
    title: str | None = None,
    kind: ScenarioKind | None = None,
    preconditions: str | None = None,
    expected: str | None = None,
    notes: str | None = None,
    status: ScenarioStatus | None = None,
    section_anchor: str | None = None,
    reason: str | None = None,
) -> Scenario:
    """Patch the given fields; unchanged fields are left alone."""
    model = _require_scenario(session, project_id, scenario_id)
    changed: dict[str, object] = {}

    if title is not None and title != model.title:
        changed["title"] = title
        model.title = title
    if kind is not None and kind.value != model.kind:
        validation.validate_scenario_kind(kind.value)
        changed["kind"] = kind.value
        model.kind = kind.value
    if preconditions is not None and preconditions != model.preconditions:
        changed["preconditions"] = preconditions
        model.preconditions = preconditions
    if expected is not None and expected != model.expected:
        changed["expected"] = expected
        model.expected = expected
    if notes is not None and notes != model.notes:
        changed["notes"] = notes
        model.notes = notes
    if status is not None and status.value != model.status:
        validation.validate_scenario_status(status.value)
        changed["status"] = status.value
        model.status = status.value
    if section_anchor is not None and section_anchor != model.section_anchor:
        _, doc_content_hash = _resolve_doc_anchor(
            session,
            project_id=project_id,
            doc_key=model.doc_key,
            section_anchor=section_anchor,
        )
        changed["section_anchor"] = section_anchor
        model.section_anchor = section_anchor
        model.doc_content_hash = doc_content_hash

    repo = ScenarioRepository(session)
    if not changed:
        current = repo.get_by_scenario_id(project_id, scenario_id)
        assert current is not None
        return current

    model.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SCENARIO,
        entity_id=model.row_id,
        author=author,
        diff=_diff("update", scenario_id=scenario_id, **changed),
        reason=reason or "update",
        activity_kind="scenario.updated",
        activity_scope_kind="scenario",
        activity_scope_id=scenario_id,
        activity_payload={"fields": sorted(changed)},
        activity_summary=f"Scenario {scenario_id} updated",
    )
    updated = repo.get_by_scenario_id(project_id, scenario_id)
    assert updated is not None
    return updated


def retire(
    session: Session,
    *,
    project_id: int,
    scenario_id: str,
    author: str,
    reason: str | None = None,
) -> Scenario:
    """Mark a scenario retired. The id is never reused and the row is kept."""
    model = _require_scenario(session, project_id, scenario_id)
    repo = ScenarioRepository(session)
    if model.status == ScenarioStatus.RETIRED.value:
        current = repo.get_by_scenario_id(project_id, scenario_id)
        assert current is not None
        return current

    old_status = model.status
    model.status = ScenarioStatus.RETIRED.value
    model.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SCENARIO,
        entity_id=model.row_id,
        author=author,
        diff=_diff(
            "retire",
            scenario_id=scenario_id,
            old=old_status,
            new=ScenarioStatus.RETIRED.value,
        ),
        reason=reason or "retire",
        activity_kind="scenario.retired",
        activity_scope_kind="scenario",
        activity_scope_id=scenario_id,
        activity_payload={"old_status": old_status},
        activity_summary=f"Scenario {scenario_id} retired",
    )
    retired = repo.get_by_scenario_id(project_id, scenario_id)
    assert retired is not None
    return retired


def group_keys(session: Session, project_id: int) -> list[str]:
    """Distinct group keys present in the project, in stable order."""
    stmt = (
        select(ScenarioModel.group_key)
        .where(ScenarioModel.project_id == project_id)
        .distinct()
        .order_by(ScenarioModel.group_key)
    )
    return list(session.execute(stmt).scalars())
