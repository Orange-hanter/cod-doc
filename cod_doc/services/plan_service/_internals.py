"""Module-internal helpers shared across the plan_service package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.domain.entities import Priority
from cod_doc.infra.models import PlanModel

from ._types import DerivedStatus, PlanNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_PRIORITY_ORDER: dict[str, int] = {
    Priority.CRITICAL.value: 0,
    Priority.HIGH.value: 1,
    Priority.MEDIUM.value: 2,
    Priority.LOW.value: 3,
}


def _require_plan(session: Session, plan_id: int) -> PlanModel:
    model = session.get(PlanModel, plan_id)
    if model is None:
        raise PlanNotFoundError(f"plan #{plan_id}")
    return model


def _derive_status(total: int, done: int, in_progress: int, cancelled: int) -> DerivedStatus:
    """Статус плана/секции по её счётчикам.

    ADO-078: `done` наступает, когда не осталось задач, требующих работы, —
    то есть когда `done + cancelled == total`, а не когда `done == total`.
    Старое условие держало план с восемью закрытыми и двумя отменёнными
    задачами из десяти в `in-progress` навсегда: взять было нечего, а
    закрыться он не мог.

    Это НЕ «считать отменённые сделанными»: `done` в счётчиках остаётся 8,
    отменённые видны отдельным полем `cancelled` (см.
    :class:`PlanProgress`). Сходится только вывод «работы не осталось».
    """
    if total == 0:
        return DerivedStatus.EMPTY
    if done + cancelled >= total:
        return DerivedStatus.DONE
    if in_progress > 0 or done > 0:
        return DerivedStatus.IN_PROGRESS
    return DerivedStatus.PENDING
