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
from .crud import group_keys, list_for_group

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# A group that never describes a failure is not described. Everything past the
# happy path and one error path is judgement, so only these two are expected.
_EXPECTED_KINDS = (ScenarioKind.HAPPY_PATH, ScenarioKind.ERROR_PATH)


def group_coverage(session: Session, project_id: int, group_key: str) -> ScenarioCoverage:
    """Count live scenarios in one group and report which expected kinds are absent."""
    scenarios = list_for_group(session, project_id, group_key)
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


def project_coverage(session: Session, project_id: int) -> list[ScenarioCoverage]:
    """Authoring coverage for every group in the project, ordered by group key."""
    return [group_coverage(session, project_id, key) for key in group_keys(session, project_id)]
