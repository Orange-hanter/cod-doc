"""Task repository — SQLAlchemy <-> domain Task entity."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from cod_doc.domain.entities import Priority, Task, TaskStatus, TaskType
from cod_doc.infra.models import TaskModel
from cod_doc.infra.repositories.base import BaseRepository


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
