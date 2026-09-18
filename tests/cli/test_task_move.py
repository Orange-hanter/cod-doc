"""CLI-поверхность переноса задачи между секциями — ``cod-doc task move``.

MCP-эквивалент (`task_move_to_section`) и сервис покрыты
tests/services/test_task_move_to_section.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import ActivityEventModel, PlanModel, PlanSectionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session


def _init_project(tmp_path: Path, name: str = "mvp") -> None:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output


def _session(name: str) -> Iterator[Session]:
    entry = Config.load().get_project(name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            yield session
    finally:
        engine.dispose()


def _seed(name: str, task_ids: tuple[str, ...] = ("MVC-001", "MVC-002")) -> None:
    """Проект с планом и двумя секциями; задачи создаются в секции A."""
    for session in _session(name):
        proj = ProjectRepository(session).get_by_slug(name)
        assert proj is not None and proj.row_id is not None
        now = datetime.now(UTC)
        plan = PlanModel(
            project_id=proj.row_id, scope=f"{name}-plan", created=now, last_updated=now
        )
        session.add(plan)
        session.flush()
        sec_a = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
        )
        sec_b = PlanSectionModel(
            plan_id=plan.row_id, letter="B", title="Web UI", slug="B-Web-UI", position=1
        )
        session.add_all([sec_a, sec_b])
        session.flush()
        for task_id in task_ids:
            tasks.create(
                session,
                project_id=proj.row_id,
                plan_id=plan.row_id,
                section_id=sec_a.row_id,
                task_id=task_id,
                title=f"Task {task_id}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )


def _section_letter(name: str, task_id: str) -> str:
    for session in _session(name):
        t = tasks.get(session, task_id)
        assert t is not None
        sec = session.get(PlanSectionModel, t.section_id)
        assert sec is not None
        return sec.letter
    raise AssertionError("unreachable")


def test_task_move_help_lists_required_options(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["task", "move", "--help"])
    assert result.exit_code == 0, result.output
    for opt in ("--plan", "--section", "--dry-run"):
        assert opt in result.output, f"{opt} отсутствует в `task move --help`"


def test_task_move_moves_batch_and_leaves_trail(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed("mvp")

    result = CliRunner().invoke(
        main,
        [
            "task",
            "move",
            "MVC-001",
            "MVC-002",
            "--project",
            "mvp",
            "--plan",
            "mvp-plan",
            "--section",
            "B",
            "--reason",
            "реструктуризация",
        ],
    )
    assert result.exit_code == 0, result.output

    assert _section_letter("mvp", "MVC-001") == "B"
    assert _section_letter("mvp", "MVC-002") == "B"

    for session in _session("mvp"):
        events = list(
            session.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "task.section_changed")
            ).scalars()
        )
        # Одно событие на задачу, не одно на батч.
        assert len(events) == 2
        assert {e.scope_id for e in events} == {"MVC-001", "MVC-002"}


def test_task_move_dry_run_changes_nothing(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed("mvp")

    result = CliRunner().invoke(
        main,
        [
            "task",
            "move",
            "MVC-001",
            "-p",
            "mvp",
            "--plan",
            "mvp-plan",
            "--section",
            "B",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _section_letter("mvp", "MVC-001") == "A"


def test_task_move_json_is_parseable(tmp_path: Path) -> None:
    """ADO-176: --json печатается через click.echo, а не rich."""
    _init_project(tmp_path)
    _seed("mvp")

    result = CliRunner().invoke(
        main,
        ["task", "move", "MVC-001", "-p", "mvp", "--plan", "mvp-plan", "--section", "B", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["moved"] == ["MVC-001"]
    assert payload["section"] == "B"
    assert payload["committed"] is True


def test_task_move_unknown_section_exits_1(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed("mvp")

    result = CliRunner().invoke(
        main, ["task", "move", "MVC-001", "-p", "mvp", "--plan", "mvp-plan", "--section", "Z"]
    )
    assert result.exit_code == 1
    assert _section_letter("mvp", "MVC-001") == "A"


def test_task_move_unknown_task_rolls_back_the_batch(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed("mvp")

    result = CliRunner().invoke(
        main,
        [
            "task",
            "move",
            "MVC-001",
            "NOPE-001",
            "-p",
            "mvp",
            "--plan",
            "mvp-plan",
            "--section",
            "B",
        ],
    )
    assert result.exit_code == 1
    # Батч атомарен: первая задача не должна уехать.
    assert _section_letter("mvp", "MVC-001") == "A"
