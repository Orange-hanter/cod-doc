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


#: Проценты рисуются целыми — как до ADO-078.
_PERCENT = 100


def _pct_closed(total: int, done: int, cancelled: int) -> int:
    """Доля закрытого в процентах: ``done`` и ``cancelled`` вместе (ADO-078).

    Страницы считали ``done / total``, и план, закрытый целиком со статусом
    DONE, рисовал «80%», если две задачи из десяти отменены. Статус и процент
    должны отвечать на один вопрос — «сколько закрыто», — а разбивку «сделано /
    отменено» несут отдельные счётчики. Формула одна на весь слой, а не по копии
    на каждый шаблон.
    """
    if total <= 0:
        return 0
    return round(_PERCENT * (done + cancelled) / total)


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
    cancelled: int
    status: DerivedStatus

    @property
    def remaining(self) -> int:
        """Сколько задач ещё требуют работы. См. :class:`PlanProgress`."""
        return max(0, self.total - self.done - self.cancelled)

    @property
    def pct_closed(self) -> int:
        """Процент закрытого для прогресс-бара — см. :func:`_pct_closed`."""
        return _pct_closed(self.total, self.done, self.cancelled)


@dataclass(slots=True)
class PlanProgress:
    plan_id: int
    scope: str
    total: int
    done: int
    in_progress: int
    cancelled: int
    status: DerivedStatus
    sections: list[SectionProgress] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        """Сколько задач ещё требуют работы (ADO-078).

        Остаток — это `total` минус ОБА закрытых состояния, а не только
        ``done``: ``task_status_machine.TERMINAL_STATUSES`` = ``{done,
        cancelled}``, и отменённая задача работы не требует. До правки
        `remaining = total - done` держал отменённые в остатке вечно — на
        живом плане `adoption-2026-08` четыре таких задачи числились
        несделанными третий месяц.

        `cancelled` при этом НЕ прибавляется к `done`: число отменённых
        видно отдельным полем, а не растворяется в «сделано» (acceptance
        ADO-078 §2). «Сделано 8, отменено 2, осталось 0» и «сделано 10» —
        разные факты о плане.

        `backlog` в остатке ОСТАЁТСЯ: это припаркованная работа, а не
        закрытая. Переход `backlog → todo` разрешён
        (``ALLOWED_TRANSITIONS``), и решение «не делать» выражается
        переводом в `cancelled`. Считать его закрытым значило бы завести
        третье определение «закрыто» рядом с ``TERMINAL_STATUSES`` и
        литералами вьюх — ровно ту рассинхронизацию, которую убирали 0038 и
        ADO-078.

        ``max(0, …)``: счётчики приходят из вьюх одной выборкой и в норме
        сходятся, но отрицательный остаток на странице хуже нуля.
        """
        return max(0, self.total - self.done - self.cancelled)

    @property
    def pct_closed(self) -> int:
        """Процент закрытого для прогресс-бара — см. :func:`_pct_closed`."""
        return _pct_closed(self.total, self.done, self.cancelled)


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
