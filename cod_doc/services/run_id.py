"""Активный `run_id` — contextvar без зависимостей (SVC-003).

Отделён от `run_context`, потому что его читают сами write-сервисы
(`activity_service`, `revision_service`, `task_doc_service`, …), а
`run_context.record_step` пишет шаг прогона через `activity_service`. Пока
contextvar жил в `run_context`, это было кольцо activity → run_context →
activity, спрятанное импортом в теле функции. Здесь — лист: модуль ничего не
импортирует из `cod_doc`.

`run_id` — телеметрия, не контракт (ADR-012): `None` здесь — нормальное
состояние, а не ошибка. Открывает скоуп `run_context.run_scope`;
`run_context` реэкспортирует оба имени, так что старые импорты работают.
"""

from __future__ import annotations

from contextvars import ContextVar

CURRENT_RUN_ID: ContextVar[str | None] = ContextVar("cod_doc_run_id", default=None)


def get_current_run_id() -> str | None:
    """Return the run_id active in the current async/thread context, or None."""
    return CURRENT_RUN_ID.get()


def set_current_run_id(run_id: str | None) -> None:
    """Override the active run_id; intended for tests / non-orchestrator contexts.

    Production code should use :func:`cod_doc.services.run_context.run_scope`
    so the value is reset on exit.
    """
    CURRENT_RUN_ID.set(run_id)
