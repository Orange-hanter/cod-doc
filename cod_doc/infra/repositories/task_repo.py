"""Task repository — SQLAlchemy <-> domain Task entity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, func, or_, select

from cod_doc.domain.entities import (
    Priority,
    Task,
    TaskStatus,
    TaskType,
    equivalent_task_statuses,
)
from cod_doc.infra.models import TaskModel
from cod_doc.infra.repositories.base import BaseRepository

if TYPE_CHECKING:
    from datetime import datetime


class TaskRepository(BaseRepository[Task, TaskModel]):
    model_cls = TaskModel

    def _to_domain(self, model: TaskModel) -> Task:
        return Task(
            row_id=model.row_id,
            project_id=model.project_id,
            task_id=model.task_id,
            plan_id=model.plan_id,
            section_id=model.section_id,
            title=model.title,
            status=TaskStatus(model.status),
            type=TaskType(model.type),
            priority=Priority(model.priority),
            description=model.description,
            acceptance=model.acceptance,
            created=model.created,
            last_updated=model.last_updated,
            completed_at=model.completed_at,
            completed_commit=model.completed_commit,
            blocked_reason=model.blocked_reason,
        )

    def _to_model(self, entity: Task) -> TaskModel:
        kwargs: dict[str, Any] = {
            "project_id": entity.project_id,
            "task_id": entity.task_id,
            "plan_id": entity.plan_id,
            "section_id": entity.section_id,
            "title": entity.title,
            "status": entity.status.value,
            "type": entity.type.value,
            "priority": entity.priority.value,
            "description": entity.description,
            "acceptance": entity.acceptance,
            "completed_commit": entity.completed_commit,
            "blocked_reason": entity.blocked_reason,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        if entity.created is not None:
            kwargs["created"] = entity.created
        if entity.last_updated is not None:
            kwargs["last_updated"] = entity.last_updated
        if entity.completed_at is not None:
            kwargs["completed_at"] = entity.completed_at
        return TaskModel(**kwargs)

    def get_by_task_id(self, task_id: str) -> Task | None:
        stmt = select(TaskModel).where(TaskModel.task_id == task_id)
        model = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(model) if model else None

    def list_for_plan(self, plan_id: int) -> list[Task]:
        stmt = (
            select(TaskModel)
            .where(TaskModel.plan_id == plan_id)
            .order_by(TaskModel.section_id, TaskModel.task_id)
        )
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]

    def _filtered(
        self,
        project_id: int,
        *,
        status: TaskStatus | None = None,
        priority: Priority | None = None,
        type: TaskType | None = None,
        plan_id: int | None = None,
        section_id: int | None = None,
        completed_since: datetime | None = None,
        updated_since: datetime | None = None,
        has_commit: bool | None = None,
    ) -> Select[tuple[TaskModel]]:
        """Build the shared WHERE for list_for_project / count_for_project (RFC 27 F7)."""
        stmt = select(TaskModel).where(TaskModel.project_id == project_id)
        if status is not None:
            # ADO-182: сравнение точной строкой резало класс эквивалентности.
            # `pending` и `todo` — один бакет по смыслу, но в базе лежат оба
            # написания одновременно, поэтому фильтр возвращал либо одну
            # группу, либо другую, и спросить «что готово к работе» целиком
            # было нельзя. Для бакетов без синонимов множество из одного
            # элемента, то есть поведение прежнее.
            stmt = stmt.where(TaskModel.status.in_(equivalent_task_statuses(status)))
        if priority is not None:
            stmt = stmt.where(TaskModel.priority == priority.value)
        if type is not None:
            stmt = stmt.where(TaskModel.type == type.value)
        if plan_id is not None:
            stmt = stmt.where(TaskModel.plan_id == plan_id)
        if section_id is not None:
            stmt = stmt.where(TaskModel.section_id == section_id)
        if completed_since is not None:
            # NULL не проходит: задача без completed_at не «закрыта после …».
            stmt = stmt.where(TaskModel.completed_at >= completed_since)
        if updated_since is not None:
            stmt = stmt.where(TaskModel.last_updated >= updated_since)
        if has_commit is True:
            stmt = stmt.where(
                TaskModel.completed_commit.is_not(None),
                TaskModel.completed_commit != "",
            )
        elif has_commit is False:
            stmt = stmt.where(
                or_(
                    TaskModel.completed_commit.is_(None),
                    TaskModel.completed_commit == "",
                )
            )
        return stmt

    def list_for_project(
        self,
        project_id: int,
        *,
        status: TaskStatus | None = None,
        priority: Priority | None = None,
        type: TaskType | None = None,
        plan_id: int | None = None,
        section_id: int | None = None,
        completed_since: datetime | None = None,
        updated_since: datetime | None = None,
        has_commit: bool | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Task]:
        stmt = self._filtered(
            project_id,
            status=status,
            priority=priority,
            type=type,
            plan_id=plan_id,
            section_id=section_id,
            completed_since=completed_since,
            updated_since=updated_since,
            has_commit=has_commit,
        )
        stmt = stmt.order_by(TaskModel.plan_id, TaskModel.section_id, TaskModel.task_id)
        if offset:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]

    def count_for_project(
        self,
        project_id: int,
        *,
        status: TaskStatus | None = None,
        priority: Priority | None = None,
        type: TaskType | None = None,
        plan_id: int | None = None,
        section_id: int | None = None,
        completed_since: datetime | None = None,
        updated_since: datetime | None = None,
        has_commit: bool | None = None,
    ) -> int:
        """Count over the same filtered set as list_for_project (total == items)."""
        stmt = self._filtered(
            project_id,
            status=status,
            priority=priority,
            type=type,
            plan_id=plan_id,
            section_id=section_id,
            completed_since=completed_since,
            updated_since=updated_since,
            has_commit=has_commit,
        )
        count_stmt = select(func.count()).select_from(stmt.subquery())
        return int(self.session.execute(count_stmt).scalar_one() or 0)
