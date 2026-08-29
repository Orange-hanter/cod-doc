"""ADO-037: адаптер legacy `/api/projects/{name}/tasks` поверх task_service.

Finding C3 контракт-аудита ADO-034: эндпоинты писали через YAML-путь
(`core/project.py`) — RuntimeError на мигрированных проектах, без Revision /
activity / статус-машины. Здесь — перевод legacy HTTP-контракта
(int-priority, status "pending"/"in-progress") на DB-сервисы. Решение
«перевести, не удалять» зафиксировано в task-doc 'design' ADO-037.

Legacy `/api/*` остаётся замороженным для новых фич (см. api/v1/__init__.py) —
этот модуль только поддерживает существующий контракт.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import Plan, PlanSection, Priority, Task, TaskStatus
from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
from cod_doc.services import plan_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Legacy-задачи не знают про планы — складываем их в служебный план проекта.
LEGACY_PLAN_SCOPE = "legacy-rest-api"
LEGACY_SECTION_LETTER = "A"
LEGACY_ID_PREFIX = "LEG"
AUTHOR = "api-legacy"

_PRIORITY_TO_INT = {
    Priority.CRITICAL: 1,
    Priority.HIGH: 3,
    Priority.MEDIUM: 5,
    Priority.LOW: 7,
}

# Границы legacy int-шкалы 1..9 → 4-значный enum.
_CRITICAL_MAX = 2
_HIGH_MAX = 4
_MEDIUM_MAX = 6

# Обратная совместимость строк статуса: legacy-клиенты ждут "pending" и
# "in-progress" (core/project.py TaskStatus), а не канонику proposal 08.
_STATUS_TO_LEGACY = {"todo": "pending", "in_progress": "in-progress"}


def priority_from_int(value: int) -> Priority:
    """Legacy priority — int 1..9 (1 = выше всех); в БД — 4-значный enum."""
    if value <= _CRITICAL_MAX:
        return Priority.CRITICAL
    if value <= _HIGH_MAX:
        return Priority.HIGH
    if value <= _MEDIUM_MAX:
        return Priority.MEDIUM
    return Priority.LOW


def priority_to_int(priority: Priority) -> int:
    return _PRIORITY_TO_INT[priority]


def status_to_legacy(status: TaskStatus) -> str:
    return _STATUS_TO_LEGACY.get(str(status), str(status))


def to_legacy_dict(task: Task) -> dict[str, Any]:
    """Ключи `core/project.py::Task.to_dict` — HTTP-контракт не меняется."""
    return {
        "id": task.task_id,
        "title": task.title,
        "description": task.description or "",
        "priority": priority_to_int(task.priority),
        "status": status_to_legacy(task.status),
        "created": task.created.isoformat() if task.created else None,
        "updated": task.last_updated.isoformat() if task.last_updated else None,
        "result": None,
        "context_refs": [],
        "blocked_by": [],
        "affects_files": [],
        "acceptance": task.acceptance,
        "story_id": None,  # story живёт в story_link, не в domain Task
    }


def ensure_legacy_plan(session: Session, project_id: int) -> tuple[int, int]:
    """Лениво создаёт служебный план/секцию для legacy-задач проекта.

    Возвращает (plan_id, section_id). Scope уникален в пределах БД проекта
    (embedded state.db), поэтому повторные вызовы идемпотентны.
    """
    plan_repo = PlanRepository(session)
    plan = plan_repo.get_by_scope(LEGACY_PLAN_SCOPE)
    if plan is None:
        plan = plan_repo.add(
            Plan(project_id=project_id, scope=LEGACY_PLAN_SCOPE, principle="from-legacy-api")
        )
        session.flush()
    assert plan.row_id is not None

    sections = plan_service.list_sections(session, plan.row_id)
    if sections:
        assert sections[0].row_id is not None
        return plan.row_id, sections[0].row_id

    section = PlanSectionRepository(session).add(
        PlanSection(
            plan_id=plan.row_id,
            letter=LEGACY_SECTION_LETTER,
            title="Legacy REST",
            slug="legacy-rest",
            position=0,
        )
    )
    session.flush()
    assert section.row_id is not None
    return plan.row_id, section.row_id
