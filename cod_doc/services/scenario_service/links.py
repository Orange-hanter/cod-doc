"""Scenario edges — what a scenario verifies, specifies or is exercised by.

Edges are deliberately loose: `to_ref` is validated for shape, not for
existence. A scenario is often written before the task that implements it and
before the capability document is imported, and refusing the edge in that
window would push authors back into free-form prose. Dangling edges are
surfaced advisorily instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import (
    EntityKind,
    ScenarioLink,
    ScenarioLinkKind,
    ScenarioRelation,
)
from cod_doc.infra.models import ScenarioLinkModel
from cod_doc.infra.repositories import ScenarioLinkRepository
from cod_doc.services import activity_service

from ._internals import _diff, _require_scenario

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def list_links(session: Session, scenario_row_id: int) -> list[ScenarioLink]:
    return ScenarioLinkRepository(session).list_for_scenario(scenario_row_id)


def link(
    session: Session,
    *,
    project_id: int,
    scenario_id: str,
    to_kind: ScenarioLinkKind,
    to_ref: str,
    relation: ScenarioRelation,
    author: str,
    reason: str | None = None,
) -> ScenarioLink:
    """Attach an edge. Re-attaching the same edge is a no-op."""
    model = _require_scenario(session, project_id, scenario_id)

    existing = session.execute(
        select(ScenarioLinkModel).where(
            ScenarioLinkModel.scenario_row_id == model.row_id,
            ScenarioLinkModel.to_kind == to_kind.value,
            ScenarioLinkModel.to_ref == to_ref,
            ScenarioLinkModel.relation == relation.value,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return ScenarioLinkRepository(session)._to_domain(existing)

    created = ScenarioLinkRepository(session).add(
        ScenarioLink(
            scenario_row_id=model.row_id,
            to_kind=to_kind,
            to_ref=to_ref,
            relation=relation,
        )
    )
    model.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SCENARIO,
        entity_id=model.row_id,
        author=author,
        diff=_diff(
            "link",
            scenario_id=scenario_id,
            to_kind=to_kind.value,
            to_ref=to_ref,
            relation=relation.value,
        ),
        reason=reason or "link",
        activity_kind="scenario.linked",
        activity_scope_kind="scenario",
        activity_scope_id=scenario_id,
        activity_payload={
            "to_kind": to_kind.value,
            "to_ref": to_ref,
            "relation": relation.value,
        },
        activity_summary=f"Scenario {scenario_id} → {to_kind.value}:{to_ref}",
    )
    return created


def unlink(
    session: Session,
    *,
    project_id: int,
    scenario_id: str,
    to_kind: ScenarioLinkKind,
    to_ref: str,
    relation: ScenarioRelation,
    author: str,
    reason: str | None = None,
) -> bool:
    """Detach an edge. Returns False when there was nothing to detach."""
    model = _require_scenario(session, project_id, scenario_id)

    existing = session.execute(
        select(ScenarioLinkModel).where(
            ScenarioLinkModel.scenario_row_id == model.row_id,
            ScenarioLinkModel.to_kind == to_kind.value,
            ScenarioLinkModel.to_ref == to_ref,
            ScenarioLinkModel.relation == relation.value,
        )
    ).scalar_one_or_none()
    if existing is None:
        return False
    session.delete(existing)

    model.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SCENARIO,
        entity_id=model.row_id,
        author=author,
        diff=_diff(
            "unlink",
            scenario_id=scenario_id,
            to_kind=to_kind.value,
            to_ref=to_ref,
            relation=relation.value,
        ),
        reason=reason or "unlink",
        activity_kind="scenario.unlinked",
        activity_scope_kind="scenario",
        activity_scope_id=scenario_id,
        activity_payload={
            "to_kind": to_kind.value,
            "to_ref": to_ref,
            "relation": relation.value,
        },
        activity_summary=f"Scenario {scenario_id} ⊘ {to_kind.value}:{to_ref}",
    )
    return True
