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


def _derive_status(total: int, done: int, in_progress: int) -> DerivedStatus:
    if total == 0:
        return DerivedStatus.EMPTY
    if done == total:
        return DerivedStatus.DONE
    if in_progress > 0 or done > 0:
        return DerivedStatus.IN_PROGRESS
    return DerivedStatus.PENDING
