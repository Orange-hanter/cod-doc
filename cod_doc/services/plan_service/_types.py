"""DTO + enum + error types for plan-service results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cod_doc.domain.entities import TaskStatus


class PlanNotFoundError(LookupError):
    pass


class TaskNotFoundInPlanError(LookupError):
    pass


class DerivedStatus(StrEnum):
    EMPTY = "empty"
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    DONE = "done"


@dataclass(slots=True)
class SectionProgress:
    section_id: int
    letter: str
    title: str
    slug: str
    position: int
    total: int
    done: int
    in_progress: int
    status: DerivedStatus

    @property
    def remaining(self) -> int:
        return self.total - self.done


@dataclass(slots=True)
class PlanProgress:
    plan_id: int
    scope: str
    total: int
    done: int
    in_progress: int
    status: DerivedStatus
    sections: list[SectionProgress] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return self.total - self.done


@dataclass(slots=True)
class PlanAuditReport:
    plan_id: int
    cycles: list[list[str]]
    done_with_unfinished_blocks: list[str]
    critical_path_length: int = 0  # populated by audit() via critical_path()

    @property
    def issues_total(self) -> int:
        return len(self.cycles) + len(self.done_with_unfinished_blocks)


@dataclass(slots=True)
class ChainEntry:
    """One task in a forward/reverse chain or critical path."""

    task_id: str
    title: str
    status: TaskStatus
    depth: int  # 0 = directly adjacent, increasing away from start


@dataclass(slots=True)
class CriticalPathResult:
    """Longest sequential chain of blocks-edges in a plan."""

    plan_id: int
    task_ids: list[str]  # ordered from source to sink
    chain: list[ChainEntry]  # same order, with metadata
    length: int  # number of tasks (0 = empty plan)
