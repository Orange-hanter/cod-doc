"""Project repository."""

from __future__ import annotations

from sqlalchemy import select

from cod_doc.domain.entities import Project
from cod_doc.infra.models import ProjectModel
from cod_doc.infra.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project, ProjectModel]):
    model_cls = ProjectModel

    def _to_domain(self, model: ProjectModel) -> Project:
        return Project(
            row_id=model.row_id,
            slug=model.slug,
            title=model.title,
            root_path=model.root_path,
            created=model.created,
            updated=model.updated,
            config=dict(model.config_json or {}),
        )

    def _to_model(self, entity: Project) -> ProjectModel:
        kwargs: dict = {
            "slug": entity.slug,
            "title": entity.title,
            "root_path": entity.root_path,
            "config_json": entity.config,
        }
        if entity.row_id is not None:
            kwargs["row_id"] = entity.row_id
        if entity.created is not None:
            kwargs["created"] = entity.created
        if entity.updated is not None:
            kwargs["updated"] = entity.updated
        return ProjectModel(**kwargs)

    def get_by_slug(self, slug: str) -> Project | None:
        stmt = select(ProjectModel).where(ProjectModel.slug == slug)
        model = self.session.execute(stmt).scalar_one_or_none()
        return self._to_domain(model) if model else None

    def list_all(self) -> list[Project]:
        stmt = select(ProjectModel).order_by(ProjectModel.slug)
        return [self._to_domain(m) for m in self.session.execute(stmt).scalars()]
