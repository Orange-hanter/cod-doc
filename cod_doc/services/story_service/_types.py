"""Coverage DTO + error types for story-service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StoryNotFoundError(LookupError):
    pass


class StoryAlreadyExistsError(ValueError):
    pass


class AcceptanceNotFoundError(LookupError):
    pass


class BrokenLinkError(ValueError):
    """Raised when `link()` target doesn't resolve in the current project."""


class CoverageStatus(StrEnum):
    """Derived coverage state. See [user-stories-graph.md §4]."""

    DRAFT = "draft"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in-progress"
    DELIVERED = "delivered"
    DEFERRED = "deferred"


@dataclass(slots=True)
class StoryCoverage:
    story_id: str
    status: CoverageStatus
    tasks_total: int
    tasks_done: int
    tasks_in_progress: int
    acceptance_total: int
    acceptance_met: int
