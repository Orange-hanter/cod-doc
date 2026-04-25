"""Base repository pattern."""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.orm import Session

DomainT = TypeVar("DomainT")
ModelT = TypeVar("ModelT")


class BaseRepository(Generic[DomainT, ModelT]):
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

    def get(self, row_id: int) -> DomainT | None:
        model = self.session.get(self.model_cls, row_id)
        return self._to_domain(model) if model else None

    def add(self, entity: DomainT) -> DomainT:
        model = self._to_model(entity)
        self.session.add(model)
        self.session.flush()  # populate row_id without committing
        return self._to_domain(model)
