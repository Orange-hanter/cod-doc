"""ADO-078: `tasks_cancelled` во вьюхах агрегатов (миграция 0039).

Спрашиваем БД на head, а не текст миграции: когда набор терминальных
статусов изменится, чинить надо новой ревизией, а тест по файлу 0039
показывал бы пальцем на замороженную. Ту же форму используют
`test_ready_tasks_cancelled.py` и `test_totals_status_aliases.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

from tests._alembic import run_alembic


@pytest.fixture
def engine_with_schema(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'totals-cancelled.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed(session, statuses: list[str]) -> tuple[int, int]:  # type: ignore[no-untyped-def]
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

    section = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
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


@pytest.mark.parametrize("view", ["section_totals", "plan_totals"])
def test_view_exposes_cancelled_column(engine_with_schema: Engine, view: str) -> None:
    """Колонка есть в живой схеме — иначе `recalc` падает на SELECT."""
    with engine_with_schema.connect() as conn:
        columns = {row[1] for row in conn.execute(text(f"PRAGMA table_info({view})")).fetchall()}
    assert "tasks_cancelled" in columns


def test_totals_count_cancelled_separately(engine_with_schema: Engine) -> None:
    """`cancelled` считается своей колонкой и НЕ попадает в `tasks_done`."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        plan_id, section_id = _seed(
            session, ["done", "cancelled", "cancelled", "todo", "in_progress"]
        )

    with engine_with_schema.connect() as conn:
        sec = conn.execute(
            text(
                "SELECT tasks_total, tasks_done, tasks_in_progress, tasks_cancelled "
                "FROM section_totals WHERE section_id = :sid"
            ),
            {"sid": section_id},
        ).one()
        plan = conn.execute(
            text(
                "SELECT tasks_total, tasks_done, tasks_in_progress, tasks_cancelled "
                "FROM plan_totals WHERE plan_id = :pid"
            ),
            {"pid": plan_id},
        ).one()

    assert tuple(sec) == (5, 1, 1, 2)
    assert tuple(plan) == (5, 1, 1, 2)


def test_column_order_is_append_only(engine_with_schema: Engine) -> None:
    """Новая колонка последняя: позиционные читатели вьюх не съезжают."""
    with engine_with_schema.connect() as conn:
        columns = [
            row[1] for row in conn.execute(text("PRAGMA table_info(plan_totals)")).fetchall()
        ]
    assert columns == [
        "plan_id",
        "tasks_total",
        "tasks_done",
        "tasks_in_progress",
        "tasks_cancelled",
    ]
