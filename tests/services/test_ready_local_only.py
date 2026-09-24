"""AFT-012 / RFC 27 F13 — ready-выборки с фильтром local_only.

Эталоны — литеральные множества task_id и числа; is_foreign в тесте для
построения ожидания не вызывается.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_ROOT = "/repo/proj"


def _seed(session: Session) -> tuple[int, int, dict[str, str]]:
    """Проект с root '/repo/proj', один план, четыре todo-задачи без блокеров.

    A — без файлов; B — относительный путь; C — смешанные; D — только чужие.
    Возвращает (project_id, plan_id, {буква: task_id}).
    """
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path=_ROOT, config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(
        project_id=proj.row_id,
        scope="p-plan",
        principle="test-first",
        created=now,
        last_updated=now,
    )
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="S", slug="A-S", position=0)
    session.add(sec)
    session.flush()

    ids: dict[str, str] = {}
    for letter, affected in [
        ("A", None),
        ("B", ["cod_doc/x.py"]),
        ("C", ["/elsewhere/z.py", "cod_doc/y.py"]),
        ("D", ["/elsewhere/a.py", "/elsewhere/b.py"]),
    ]:
        task_id = f"LOC-00{ord(letter) - ord('A') + 1}"
        tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=sec.row_id,
            task_id=task_id,
            title=f"Task {letter}",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
            affected_files=affected,
        )
        ids[letter] = task_id
    return proj.row_id, plan.row_id, ids


def test_ready_for_project_local_only_drops_only_all_foreign(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _plan_id, ids = _seed(session)

        local = plans.ready_for_project(session, pid)
        assert {t.task_id for t in local} == {ids["A"], ids["B"], ids["C"]}

        everything = plans.ready_for_project(session, pid, local_only=False)
        assert {t.task_id for t in everything} == {
            ids["A"],
            ids["B"],
            ids["C"],
            ids["D"],
        }


def test_ready_batch_counts_skipped(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plan_id, ids = _seed(session)

        batch_project = plans.ready_batch_for_project(session, pid)
        assert batch_project.skipped_foreign == 1
        assert {t.task_id for t in batch_project.tasks} == {ids["A"], ids["B"], ids["C"]}

        batch_plan = plans.ready_batch(session, plan_id)
        assert batch_plan.skipped_foreign == 1
        assert {t.task_id for t in batch_plan.tasks} == {ids["A"], ids["B"], ids["C"]}

        assert plans.ready_batch_for_project(session, pid, local_only=False).skipped_foreign == 0
        assert plans.ready_batch(session, plan_id, local_only=False).skipped_foreign == 0


def test_limit_applies_after_filter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Чужая D идёт первой (priority critical), но limit срезает ПОСЛЕ фильтра."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plan_id, ids = _seed(session)
        tasks.update_priority(
            session,
            task_id=ids["D"],
            new_priority=Priority.CRITICAL,
            author="human:test",
        )

        first = plans.ready_for_project(session, pid, limit=1)
        assert len(first) == 1
        assert first[0].task_id != ids["D"]

        first_plan = plans.ready(session, plan_id, limit=1)
        assert len(first_plan) == 1
        assert first_plan[0].task_id != ids["D"]
