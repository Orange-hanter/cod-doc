"""Authoring coverage — how well a group is *described*, never how well it is tested.

The distinction matters enough to repeat: this module counts scenarios that
have been written down. Whether a test proves any of them is [RFC 24 §9]
evidence, computed by the structure producer and stored in
``scenario_assessment`` (STR-002). Nothing here may be presented as test
coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.domain.entities import ScenarioKind, ScenarioStatus

from ._types import ScenarioCoverage
from .crud import list_for_group, list_for_project

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Scenario

# A group that never describes a failure is not described. Everything past the
# happy path and one error path is judgement, so only these two are expected.
_EXPECTED_KINDS = (ScenarioKind.HAPPY_PATH, ScenarioKind.ERROR_PATH)


def coverage_for_rows(group_key: str, scenarios: list[Scenario]) -> ScenarioCoverage:
    """Count one group's rows. Pure — no session, no query.

    Split out of ``group_coverage`` so ``project_coverage`` can cover a whole
    project from a single ``list_for_project`` instead of one query per group
    (the web list page reads every group at once).
    """
    live = [s for s in scenarios if s.status is not ScenarioStatus.RETIRED]

    by_kind: dict[str, int] = {}
    for scenario in live:
        by_kind[scenario.kind.value] = by_kind.get(scenario.kind.value, 0) + 1

    return ScenarioCoverage(
        group_key=group_key,
        total=len(live),
        by_kind=dict(sorted(by_kind.items())),
        missing_kinds=[k.value for k in _EXPECTED_KINDS if k.value not in by_kind],
        confirmed=sum(1 for s in live if s.status is ScenarioStatus.CONFIRMED),
        draft=sum(1 for s in live if s.status is ScenarioStatus.DRAFT),
        retired=len(scenarios) - len(live),
    )


def group_coverage(session: Session, project_id: int, group_key: str) -> ScenarioCoverage:
    """Count live scenarios in one group and report which expected kinds are absent."""
    return coverage_for_rows(group_key, list_for_group(session, project_id, group_key))


def project_coverage(session: Session, project_id: int) -> list[ScenarioCoverage]:
    """Authoring coverage for every group in the project, ordered by group key.

    One query: the repository already returns rows ordered by
    ``group_key, position, scenario_id``, so grouping is a single pass.
    """
    grouped: dict[str, list[Scenario]] = {}
    for scenario in list_for_project(session, project_id):
        grouped.setdefault(scenario.group_key, []).append(scenario)
    return [coverage_for_rows(key, grouped[key]) for key in sorted(grouped)]
