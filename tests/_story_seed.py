"""Сид для тестов покрытия историй: проект и план с одной секцией.

Общий для ``tests/services/test_story_coverage.py`` и
``tests/services/test_story_coverage_cancelled.py``. Пишется прямо в ORM:
покрытию нужны только строки, сервисы создания тут не проверяются.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def seed_plan_with_section(session: Session, project_id: int) -> tuple[int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="X", slug="A-X", position=0)
    session.add(sec)
    session.flush()
    return plan.row_id, sec.row_id
