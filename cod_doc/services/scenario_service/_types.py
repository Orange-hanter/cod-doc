"""Error types + authoring-coverage DTO for scenario-service."""

from __future__ import annotations

from dataclasses import dataclass, field


class ScenarioNotFoundError(LookupError):
    pass


class ScenarioAlreadyExistsError(ValueError):
    pass


class ScenarioGroupError(ValueError):
    """Raised when a group key is malformed or already owned by another anchor."""


class CoverageStatusNotOwnedError(ValueError):
    """Raised when a caller tries to set an [RFC 24 §9] coverage verdict as a status.

    ``covered | partial | missing | unverifiable`` are evidence derived by the
    structure producer under rules cod-doc cannot enforce on hand-typed input
    (an aggregate-LCOV ceiling, a mandatory ``statusReason``, ``unresolved``
    never decaying into ``missing``). They live in ``scenario_assessment``
    (STR-002), never in ``scenario.status``.
    """


@dataclass(slots=True)
class ScenarioCoverage:
    """Authoring coverage of one group — how well the group is *described*.

    This is deliberately not test coverage: it counts what has been written
    down, never what a test proves.
    """

    group_key: str
    total: int
    by_kind: dict[str, int] = field(default_factory=dict)
    missing_kinds: list[str] = field(default_factory=list)
    confirmed: int = 0
    draft: int = 0
    retired: int = 0
