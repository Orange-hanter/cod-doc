"""0035: view'ы task-агрегатов не должны зависеть от написания статуса.

`pending` ≡ `todo` и `in-progress` ≡ `in_progress` — один бакет
(`domain.entities.TASK_STATUS_ALIASES`), и в живой БД лежат оба написания
сразу. Пока `section_totals` / `plan_totals` / `ready_tasks` сравнивали
статус точной строкой, половина класса эквивалентности была для них
невидима.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from cod_doc.domain.entities import TaskStatus, equivalent_task_statuses
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services.plan_service import reads as plan_reads
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def engine_with_schema(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'totals.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed(session, statuses: list[str]) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """Один проект / план / секция и по задаче на каждый статус."""
    now = datetime.now(UTC)
    project = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    project.created = now
    project.updated = now
    session.add(project)
    session.flush()

    plan = PlanModel(
        project_id=project.row_id,
        scope="p-plan",
        principle="test-first",
        created=now,
        last_updated=now,
    )
    session.add(plan)
    session.flush()

    section = PlanSectionModel(
        plan_id=plan.row_id,
        letter="A",
        title="A",
        slug="A",
        position=0,
    )
    session.add(section)
    session.flush()

    for idx, status in enumerate(statuses):
        session.add(
            TaskModel(
                project_id=project.row_id,
                task_id=f"P-{idx:03d}",
                plan_id=plan.row_id,
                section_id=section.row_id,
                title=f"Task {idx}",
                status=status,
                type="feature",
                priority="medium",
                created=now,
                last_updated=now,
            )
        )
    session.flush()
    return plan.row_id, section.row_id


def _view_sql(engine, name: str) -> str:  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return str(
            conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='view' AND name = :n"),
                {"n": name},
            ).scalar_one()
        )


def test_totals_count_both_in_progress_spellings(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Задача, взятая в работу из `todo`, хранится как `in_progress` — и тоже «в работе»."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, section_id = _seed(
            session, ["in-progress", "in_progress", "todo", "pending", "done"]
        )

    with engine_with_schema.connect() as conn:
        sec_row = conn.execute(
            text(
                "SELECT tasks_total, tasks_done, tasks_in_progress "
                "FROM section_totals WHERE section_id = :sid"
            ),
            {"sid": section_id},
        ).one()
        plan_row = conn.execute(
            text(
                "SELECT tasks_total, tasks_done, tasks_in_progress "
                "FROM plan_totals WHERE plan_id = :pid"
            ),
            {"pid": plan_id},
        ).one()

    assert tuple(sec_row) == (5, 1, 2)
    assert tuple(plan_row) == (5, 1, 2)


def test_plan_progress_sees_canonical_in_progress(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Сквозь сервисный слой: `in_progress` попадает в PlanProgress, а не теряется."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, _ = _seed(session, ["in_progress", "todo"])

    with transactional(factory) as session:
        progress = plan_reads.recalc(session, plan_id)

    assert progress.in_progress == 1
    assert progress.sections[0].in_progress == 1
    assert progress.status == "in-progress"


def test_ready_tasks_includes_both_ready_spellings(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`todo` — такой же «готов к работе», как легаси `pending` (ADO-182)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session, ["pending", "todo", "in_progress", "backlog", "done"])

    with engine_with_schema.connect() as conn:
        ready = {r[0] for r in conn.execute(text("SELECT task_id FROM ready_tasks")).fetchall()}

    assert ready == {"P-000", "P-001"}


@pytest.mark.parametrize(
    ("view", "status"),
    [
        ("section_totals", TaskStatus.IN_PROGRESS_NEW),
        ("plan_totals", TaskStatus.IN_PROGRESS_NEW),
        ("ready_tasks", TaskStatus.TODO),
    ],
)
def test_view_sql_lists_every_alias(engine_with_schema, view: str, status: TaskStatus) -> None:  # type: ignore[no-untyped-def]
    """Anti-drift: новый алиас в TASK_STATUS_ALIASES обязан попасть во view.

    Ловит ровно ту ошибку, ради которой написана миграция 0035: view
    сравнивает `task.status` с частью класса эквивалентности, а не со
    всем классом. Добавили алиас в `domain.entities` и забыли миграцию —
    падает здесь, а не молчаливой недостачей в Progress Overview.
    """
    sql = _view_sql(engine_with_schema, view)
    literals = set(re.findall(r"'([^']*)'", sql))
    missing = equivalent_task_statuses(status) - literals
    assert not missing, f"{view} не учитывает написания статуса: {sorted(missing)}"
