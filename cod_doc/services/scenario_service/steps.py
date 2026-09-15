"""Scenario steps — replace-all and append.

Steps are revisioned under their parent ``EntityKind.SCENARIO`` (the same
shape acceptance criteria use under a story), so a step edit shows up in the
scenario's revision history rather than in a namespace of its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select

from cod_doc.domain.entities import EntityKind, ScenarioStep
from cod_doc.infra.models import ScenarioStepModel
from cod_doc.infra.repositories import ScenarioStepRepository
from cod_doc.services import activity_service, validation

from ._internals import _diff, _require_scenario

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def list_steps(session: Session, scenario_row_id: int) -> list[ScenarioStep]:
    return ScenarioStepRepository(session).list_for_scenario(scenario_row_id)


def set_steps(
    session: Session,
    *,
    project_id: int,
    scenario_id: str,
    steps: list[str],
    author: str,
    reason: str | None = None,
) -> list[ScenarioStep]:
    """Replace every step of the scenario, renumbering positions from zero."""
    model = _require_scenario(session, project_id, scenario_id)
    validation.validate_scenario_body(
        preconditions=model.preconditions, expected=model.expected, steps=steps
    )

    old_count = (
        session.execute(
            select(func.count())
            .select_from(ScenarioStepModel)
            .where(ScenarioStepModel.scenario_row_id == model.row_id)
        ).scalar_one()
        or 0
    )
    session.execute(
        delete(ScenarioStepModel).where(ScenarioStepModel.scenario_row_id == model.row_id)
    )
    for i, text in enumerate(steps):
        session.add(ScenarioStepModel(scenario_row_id=model.row_id, position=i, text=text))
    model.last_updated = datetime.now(UTC)
    session.flush()

    activity_service.write_revision_and_emit_event(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SCENARIO,
        entity_id=model.row_id,
        author=author,
        diff=_diff("set_steps", scenario_id=scenario_id, old=old_count, new=len(steps)),
        reason=reason or "set_steps",
        activity_kind="scenario.steps_set",
        activity_scope_kind="scenario",
        activity_scope_id=scenario_id,
        activity_payload={"old_count": old_count, "new_count": len(steps)},
        activity_summary=f"Scenario {scenario_id}: {len(steps)} step(s) set",
    )
    return ScenarioStepRepository(session).list_for_scenario(model.row_id)


def add_step(
    session: Session,
    *,
    project_id: int,
    scenario_id: str,
    text: str,
    author: str,
    reason: str | None = None,
) -> list[ScenarioStep]:
    """Append one step at the end of the scenario."""
    model = _require_scenario(session, project_id, scenario_id)
    current = [s.text for s in ScenarioStepRepository(session).list_for_scenario(model.row_id)]
    return set_steps(
        session,
        project_id=project_id,
        scenario_id=scenario_id,
        steps=[*current, text],
        author=author,
        reason=reason or "add_step",
    )
