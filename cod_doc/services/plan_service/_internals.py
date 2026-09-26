"""Module-internal helpers shared across the plan_service package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.domain.entities import PlanSection, Priority
from cod_doc.infra.models import PlanModel
from cod_doc.services.validation import (
    ValidationIssue,
    audit_html_escaped_text,
    plan_section_slug,
    validate_plan_section_letter,
    validate_plan_section_position,
    validate_plan_section_title,
    validate_section_slug,
)

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


def build_section(
    *,
    plan_id: int,
    letter: str,
    title: str,
    slug: str | None,
    position: int,
) -> tuple[PlanSection, list[ValidationIssue]]:
    """Собрать проверенную секцию плана для записи; в БД ничего не пишет.

    Буква приводится к верхнему регистру и проверяется вместе с заголовком и
    позицией; явный ``slug`` обязан пройти :func:`validate_section_slug`, без
    него слаг даёт :func:`plan_section_slug`. Нарушение — ``ValidationError``.

    Вторым элементом — advisory PS-004 на HTML-сущности в заголовке: заголовок
    не заменяется, вызывающий только показывает предупреждение.

    Валидация — только на записи. 16 слагов вне конвенции, уже лежащих в
    живой БД cod-doc (вроде ``Structure protocol (RFC 24)``), читаются как
    есть: чтение секций ничего не проверяет.
    """
    letter = letter.strip().upper()
    validate_plan_section_letter(letter)
    validate_plan_section_title(title)
    validate_plan_section_position(position)
    if slug is not None:
        slug = slug.strip()
        validate_section_slug(slug)
    else:
        slug = plan_section_slug(letter, title)
    section = PlanSection(plan_id=plan_id, letter=letter, title=title, slug=slug, position=position)
    return section, audit_html_escaped_text(title, field="title")
