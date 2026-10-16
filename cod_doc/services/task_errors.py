"""Ошибки задач и захвата — лист без зависимостей (SVC-004).

`checkout_service` импортировал `task_service` ради `TaskNotFoundError`, а
`task_service` — `checkout_service` ради `CheckoutConflictError`: кольцо из
двух модулей, где нет ни одного вызова, только классы исключений.
`task_service` и `checkout_service` реэкспортируют эти классы под прежними
именами.
"""

from __future__ import annotations


class TaskNotFoundError(LookupError):
    pass


class CheckoutConflictError(RuntimeError):
    """Raised when the task is already locked by a different actor."""

    def __init__(self, *, task_id: str, locked_by: str) -> None:
        super().__init__(
            f"Task {task_id!r} is checked out by {locked_by!r}; never retry on conflict"
        )
        self.task_id = task_id
        self.locked_by = locked_by


class CheckoutStatusError(RuntimeError):
    """Raised when the current task status is not in ``expected_statuses``."""

    def __init__(self, *, task_id: str, current: str, expected: list[str]) -> None:
        super().__init__(f"Task {task_id!r} is in {current!r}, not in expected {expected!r}")
        self.task_id = task_id
        self.current = current
        self.expected = expected
