"""AFT-013: ``task_service.complete`` не снимает замок checkout — снимает release.

Скилл task-flow велит после ``task_complete`` звать ``task_release``; этот
тест фиксирует поведение, на котором стоит совет. Эталон — строка
``task.checked_out_by`` в БД, прочитанная прямым SELECT.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import checkout_service, task_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


def _checked_out_by(factory: sessionmaker[Session], task_id: str) -> str | None:
    with transactional(factory) as s:
        return s.execute(
            select(TaskModel.checked_out_by).where(TaskModel.task_id == task_id)
        ).scalar_one()


def test_complete_keeps_lock_until_release(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)
    with transactional(factory) as s:
        proj = ProjectModel(slug="lck", title="LCK", root_path="/tmp/lck", config_json={})
        proj.created = now
        proj.updated = now
        s.add(proj)
        s.flush()
        plan = PlanModel(project_id=proj.row_id, scope="lck-plan", created=now, last_updated=now)
        s.add(plan)
        s.flush()
        sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
        s.add(sec)
        s.flush()
        task_service.create(
            s,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=sec.row_id,
            task_id="LCK-001",
            title="Lock semantics",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )

    with transactional(factory) as s:
        checkout_service.checkout(s, task_id="LCK-001", agent="claude-x")
    with transactional(factory) as s:
        task_service.complete(s, task_id="LCK-001", author="claude-x", commit_sha="abc1234")

    assert _checked_out_by(factory, "LCK-001") == "claude-x"

    with transactional(factory) as s:
        checkout_service.release(s, task_id="LCK-001", agent="claude-x")

    assert _checked_out_by(factory, "LCK-001") is None
