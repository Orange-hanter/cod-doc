"""Сид STO-023: два проекта с одинаковыми ``plan.scope``, ``story_id`` и ``module_id``.

Общий для сервисных (``tests/services/test_project_scoped_lookups.py``) и
MCP-тестов (``tests/test_project_scoped_tools_mcp.py``).

Планы и истории сеются через сервисы: ``create_plan`` и
``story_service.create`` сами проверяют уникальность ключа и до STO-023
отвергали второй проект — сид через них ловит и эту регрессию. Модуль
пишется прямо в ORM: сервиса создания модулей нет.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import Priority
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ModuleModel, ProjectModel
from cod_doc.services import plan_service
from cod_doc.services import story_service as stories

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

SCOPE = "cod-doc-bootstrap"
STORY = "US-001"
MODULE = "M1-auth"
SLUGS = ("alpha", "beta")


def seed_two_projects(engine: Engine) -> tuple[sessionmaker[Session], dict[str, int]]:
    """Два проекта с одинаковыми scope, story_id и module_id."""
    factory = make_session_factory(engine)
    pids: dict[str, int] = {}
    with transactional(factory) as session:
        for slug in SLUGS:
            now = datetime.now(UTC)
            proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
            proj.created = now
            proj.updated = now
            session.add(proj)
            session.flush()
            pid = proj.row_id
            pids[slug] = pid
            plan_service.create_plan(
                session,
                project_id=pid,
                scope=SCOPE,
                principle=slug,
                author="human:test",
            )
            plan_service.create_section(
                session,
                project_id=pid,
                plan_scope=SCOPE,
                letter="A",
                title=f"Core {slug}",
                author="human:test",
            )
            stories.create(
                session,
                project_id=pid,
                story_id=STORY,
                persona=f"persona-{slug}",
                narrative="As X, I want Y, so Z.",
                priority=Priority.MEDIUM,
                author="human:test",
            )
            session.add(ModuleModel(project_id=pid, module_id=MODULE, name=slug, status="active"))
            session.flush()
    return factory, pids
