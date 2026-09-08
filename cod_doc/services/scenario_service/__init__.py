"""Test scenarios — the authoring half of [RFC 24 §9].

RFC 24 splits the scenario problem in two, and this package owns exactly one
side of that split:

- **Intention (here).** What should be true: a title, a scenario kind from the
  RFC's five-value vocabulary, preconditions, ordered steps, an expected
  result, and an anchor into the capability document that states the
  obligation. Authored by a human or an agent; the claim status is
  ``draft | confirmed | retired`` per RFC 24 §8.
- **Evidence (not here).** Whether a test actually proves the claim. RFC 24 §9's
  ``covered | partial | missing | unverifiable`` verdicts are derived by the
  structure producer in ai-reviewer under rules that cannot be enforced on
  hand-typed input, so they never become a column on ``scenario``: STR-002
  stores them in an append-only ``scenario_assessment`` keyed on
  ``scenario.row_id``.

The three tables this package writes are the "normalized scenario index"
RFC 24 §12 defers to a later phase — they occupy that slot rather than
competing with it, so STR-001…STR-004 join onto them instead of reworking them.
"""

from ._types import (
    CoverageStatusNotOwnedError,
    ScenarioAlreadyExistsError,
    ScenarioCoverage,
    ScenarioGroupError,
    ScenarioNotFoundError,
)
from .crud import (
    create,
    get,
    group_keys,
    list_for_group,
    list_for_project,
    retire,
    update,
)
from .steps import add_step, list_steps, set_steps

__all__ = [
    "CoverageStatusNotOwnedError",
    "ScenarioAlreadyExistsError",
    "ScenarioCoverage",
    "ScenarioGroupError",
    "ScenarioNotFoundError",
    "add_step",
    "create",
    "get",
    "group_keys",
    "list_for_group",
    "list_for_project",
    "list_steps",
    "retire",
    "set_steps",
    "update",
]
