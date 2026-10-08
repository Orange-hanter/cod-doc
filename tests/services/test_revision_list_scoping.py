"""Лента ревизий скоупится проектом и при фильтре по сущности.

`/p/{slug}/revisions?entity_kind=…&entity_id=…` зовёт
`revision_service.list_for_project`: `entity_id` — row_id внутри проекта, и в
общей (hub) БД у двух проектов бывают сущности с одинаковым row_id. Фильтр не
должен отдать ревизии чужого проекта.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import revision_service

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def _project(session: Session, slug: str) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def test_entity_filter_does_not_cross_projects(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        mine = _project(session, "mine")
        other = _project(session, "other")
        for pid, diff in ((mine, '{"op": "mine"}'), (other, '{"op": "other"}')):
            revision_service.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.ADR,
                entity_id=7,
                author="human:test",
                diff=diff,
            )
        rows = revision_service.list_for_project(
            session, mine, entity_kind=EntityKind.ADR, entity_id=7
        )
    assert [r.diff for r in rows] == ['{"op": "mine"}']
