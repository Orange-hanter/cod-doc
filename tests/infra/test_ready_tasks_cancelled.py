"""ADO-078: `cancelled` закрыт наравне с `done` — во view и в Python.

Anti-drift: миграция 0038 повторяет набор закрытых статусов литералами
(застывший снимок схемы), а `task_status_machine.TERMINAL_STATUSES` —
единственная точка вывода для Python-слоя. Если набор разъедется, здесь
станет красно.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services.task_status_machine import TERMINAL_STATUSES
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine


@pytest.fixture
def engine_with_schema(tmp_path: Path) -> Iterator[Engine]:
    """Свежая SQLite со схемой на head — как в test_totals_status_aliases."""
    db_url = f"sqlite:///{tmp_path / 'ready.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _view_sql(engine: Engine, name: str) -> str:
    """Текст view, как его хранит сама БД."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='view' AND name=:n"), {"n": name}
        ).fetchone()
    assert row is not None, f"view {name} не создан"
    return str(row[0])


def test_live_view_knows_every_terminal_status(engine_with_schema: Engine) -> None:
    """Живой `ready_tasks` перечисляет весь набор закрытых статусов.

    Спрашиваем БД на head, а не застывший текст миграции 0038. Разница
    практическая: когда закрытых статусов станет больше, чинить надо новой
    миграцией, а тест, ассертящий по файлу 0038, показывал бы пальцем на
    замороженную ревизию — то есть на файл, который править нельзя.
    Ту же форму использует `test_totals_status_aliases.py`.
    """
    sql = _view_sql(engine_with_schema, "ready_tasks")
    for status in TERMINAL_STATUSES:
        assert f"'{status}'" in sql, f"view не знает про закрытый статус {status!r}"


def test_live_view_does_not_compare_against_done_alone(engine_with_schema: Engine) -> None:
    """Точное сравнение с `done` ушло — иначе отменённый блокер снова запрёт."""
    sql = _view_sql(engine_with_schema, "ready_tasks")
    assert "dep.status <> 'done'" not in sql


def test_cancelled_blocker_no_longer_holds_dependents(engine_with_schema: Engine) -> None:
    """Задача, чей единственный блокер отменён, попадает в ready_tasks."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
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

        rows: dict[str, TaskModel] = {}
        for task_id, status in (
            ("T-001", "cancelled"),
            ("T-002", "todo"),
            ("T-003", "blocked"),
        ):
            model = TaskModel(
                project_id=project.row_id,
                task_id=task_id,
                plan_id=plan.row_id,
                section_id=section.row_id,
                title=task_id,
                status=status,
                type="feature",
                priority="medium",
                created=now,
                last_updated=now,
            )
            session.add(model)
            rows[task_id] = model
        session.flush()

        # T-002 блокируется отменённой T-001.
        session.add(
            DependencyModel(
                from_task_id=rows["T-002"].row_id,
                to_task_id=rows["T-001"].row_id,
                kind="blocks",
            )
        )

    with engine_with_schema.connect() as conn:
        ready = {r[0] for r in conn.execute(text("SELECT task_id FROM ready_tasks"))}

    assert "T-002" in ready, "отменённый блокер всё ещё держит зависимую задачу"
    assert "T-001" not in ready, "отменённая задача сама не может быть готова к старту"
    assert "T-003" not in ready, "задача в статусе blocked не готова к старту"
