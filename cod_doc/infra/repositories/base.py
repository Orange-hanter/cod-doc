"""Base repository pattern."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def require_int_project_id(project_id: object) -> int:
    """Явный отказ на не-``int`` ``project_id`` в поиске по проектному ключу.

    ``scope``/``story_id`` уникальны только в проекте (STO-018/024). С
    ``project_id=None`` SQLAlchemy молча строит ``project_id IS NULL`` и
    отдаёт ``None`` — неотличимо от «не найдено». ``bool`` — подкласс
    ``int``, но проектом не бывает.
    """
    if not isinstance(project_id, int) or isinstance(project_id, bool):
        raise TypeError(f"project_id must be int, got {type(project_id).__name__}")
    return project_id


class BaseRepository[DomainT, ModelT]:
    """Generic repository: SQLAlchemy model <-> domain entity.

    Subclasses implement `_to_domain(model)` and `_to_model(entity)`.
    """

    model_cls: type[ModelT]

    def __init__(self, session: Session) -> None:
        self.session = session

    def _to_domain(self, model: ModelT) -> DomainT:
        raise NotImplementedError

    def _to_model(self, entity: DomainT) -> ModelT:
        raise NotImplementedError

    def to_domain(self, model: ModelT) -> DomainT:
        """Публичный маппер ORM → домен для строк, выбранных сервисом своим запросом."""
        return self._to_domain(model)

    def get(self, row_id: int) -> DomainT | None:
        model = self.session.get(self.model_cls, row_id)
        return self._to_domain(model) if model else None

    def add(self, entity: DomainT) -> DomainT:
        model = self._to_model(entity)
        self.session.add(model)
        self.session.flush()  # populate row_id without committing
        return self._to_domain(model)
