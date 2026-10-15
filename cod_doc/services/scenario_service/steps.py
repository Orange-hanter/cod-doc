"""Scenario steps — replace-all and append.

Steps are revisioned under their parent ``EntityKind.SCENARIO`` (the same
shape acceptance criteria use under a story), so a step edit shows up in the
scenario's revision history rather than in a namespace of its own.
"""

from __future__ import annotations

import difflib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import EntityKind, ScenarioStep
from cod_doc.infra import ordering
from cod_doc.infra.models import ScenarioStepModel
from cod_doc.infra.rank import initial
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
    """Replace the scenario's step list, renumbering positions from zero.

    ORM-дифф старого списка с новым (CLO-007), а не удаление и вставка всех
    шагов: строка — а с ней ``uid`` и ``rank`` — остаётся у каждого шага,
    который пережил правку, и хук захвата пишет операции только о том, что
    изменилось (см. :func:`_diff_steps`).
    """
    model = _require_scenario(session, project_id, scenario_id)
    validation.validate_scenario_body(
        preconditions=model.preconditions, expected=model.expected, steps=steps
    )

    rows = ordering.siblings(session, ScenarioStepModel, scenario_row_id=model.row_id)
    old_count = len(rows)
    ordered, removed = _diff_steps(rows, steps, scenario_row_id=model.row_id)
    for row in removed:
        session.delete(row)
    for row in ordered:
        if row not in rows:
            session.add(row)
    ordering.renumber(ordered)
    model.last_updated = datetime.now(UTC)
    session.flush()
    # Шаги менялись мимо коллекции ``model.steps``; загруженная — устарела.
    session.expire(model, ["steps"])

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


def _diff_steps(
    rows: list[ScenarioStepModel], steps: list[str], *, scenario_row_id: int
) -> tuple[list[ScenarioStepModel], list[ScenarioStepModel]]:
    """Сопоставить шаги старого списка новому: (новый порядок строк, удалённые).

    Сопоставление — по тексту, через наибольшую общую подпоследовательность
    (``difflib.SequenceMatcher``): шаг с тем же текстом остаётся своей
    строкой, где бы ни вставили или удалили соседей. По позиции сопоставлять
    нельзя: вставка шага в начало сдвинула бы все остальные, и каждый получил
    бы «правку текста» — на другой реплике это конфликт на каждом шаге.

    Внутри изменённого куска (``replace``) старые и новые шаги парятся по
    порядку: переформулированный шаг — тот же шаг, ``uid`` у него остаётся,
    уезжает правка ``text``. Лишние старые — удаление, лишние новые — вставка.

    Ранги — ``ordering.conform``: сохранённые шаги идут в прежнем порядке,
    их ранги не трогаются, новые получают ранг между соседями. Если не
    сохранился ни один шаг, ранги — ``rank.initial``: равномерно, с запасом
    под вставки, как раньше при замене целиком.
    """
    matcher = difflib.SequenceMatcher(a=[r.text for r in rows], b=steps, autojunk=False)
    ordered: list[ScenarioStepModel] = []
    removed: list[ScenarioStepModel] = []
    fresh: list[ScenarioStepModel] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old, new = rows[i1:i2], steps[j1:j2]
        if tag == "equal":
            ordered.extend(old)
            continue
        for row, text in zip(old, new, strict=False):
            row.text = text
            ordered.append(row)
        removed.extend(old[len(new) :])
        for text in new[len(old) :]:
            step = ScenarioStepModel(scenario_row_id=scenario_row_id, position=0, text=text)
            ordered.append(step)
            fresh.append(step)
    if len(fresh) == len(ordered):
        for step, step_rank in zip(ordered, initial(len(ordered)), strict=True):
            step.rank = step_rank
    else:
        ordering.conform(ordered)
    return ordered, removed


def add_step(
    session: Session,
    *,
    project_id: int,
    scenario_id: str,
    text: str,
    author: str,
    reason: str | None = None,
    position: int | None = None,
) -> list[ScenarioStep]:
    """Вставить один шаг: в конец или на индекс ``position`` (CLO-004, SCN-093).

    Пишется одна строка с одним рангом между соседями; ранги остальных шагов не
    меняются. До CLO-004 шаг дописывался через ``set_steps`` — удаление и
    пересоздание всех шагов, то есть новые строки и новые ранги у каждого:
    две реплики, добавившие по шагу, конфликтовали бы на каждом. ``position``
    шагов — индекс, он пересчитывается из ранга.
    """
    model = _require_scenario(session, project_id, scenario_id)
    rows = ordering.siblings(session, ScenarioStepModel, scenario_row_id=model.row_id)
    current = [r.text for r in rows]
    validation.validate_scenario_body(
        preconditions=model.preconditions, expected=model.expected, steps=[*current, text]
    )
    if position is not None and not 0 <= position <= len(rows):
        raise ValueError(f"position {position} is out of range 0…{len(rows)} for {scenario_id}")
    step = ScenarioStepModel(scenario_row_id=model.row_id, position=len(rows), text=text)
    ordered = ordering.place([*rows, step], step, position)
    session.add(step)
    ordering.renumber(ordered)
    model.last_updated = datetime.now(UTC)
    session.flush()

    index = ordered.index(step)
    activity_service.write_revision_and_emit_event(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SCENARIO,
        entity_id=model.row_id,
        author=author,
        diff=_diff(
            "set_steps", scenario_id=scenario_id, old=len(rows), new=len(rows) + 1, position=index
        ),
        reason=reason or "add_step",
        activity_kind="scenario.steps_set",
        activity_scope_kind="scenario",
        activity_scope_id=scenario_id,
        activity_payload={"old_count": len(rows), "new_count": len(rows) + 1, "position": index},
        activity_summary=f"Scenario {scenario_id}: {len(rows) + 1} step(s) set",
    )
    return ScenarioStepRepository(session).list_for_scenario(model.row_id)
