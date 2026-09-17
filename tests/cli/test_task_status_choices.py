"""ADO-178: CLI обязан знать все статусы, но не обходить протокол.

До этого `task list -s` и `task status` принимали три значения из девяти, и
`cancelled`/`todo` — уже лежащие в живой базе — из CLI были недостижимы.

Расширение списка нельзя делать «в лоб»: ADO-039 требует, чтобы переход
`todo → in_progress` шёл только через `task_checkout`. Поэтому Choice
пропускает значение, а отказывает машина состояний — и её сообщение
объясняет причину, вместо «нет такого статуса».
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.cli.task import _STATUS_VALUES
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "stat"


def _seed(tmp_path: Path, statuses: dict[str, TaskStatus]) -> str:
    """Проект с задачами в заданных статусах: {task_id: status}."""
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output

    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            project = ProjectRepository(session).get_by_slug(_PROJECT)
            assert project is not None and project.row_id is not None
            now = datetime.now(UTC)
            plan = PlanModel(
                project_id=project.row_id, scope=f"{_PROJECT}-plan", created=now, last_updated=now
            )
            session.add(plan)
            session.flush()
            section = PlanSectionModel(
                plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
            )
            session.add(section)
            session.flush()
            for task_id, status in statuses.items():
                task_service.create(
                    session,
                    project_id=project.row_id,
                    plan_id=plan.row_id,
                    section_id=section.row_id,
                    task_id=task_id,
                    title=f"задача {task_id}",
                    type=TaskType.FEATURE,
                    priority=Priority.LOW,
                    author="test",
                )
                if status is not TaskStatus.PENDING:
                    task_service.update_status(
                        session,
                        task_id=task_id,
                        new_status=status,
                        author="test",
                    )
    finally:
        engine.dispose()
    return _PROJECT


# ── состав списка ───────────────────────────────────────────────────────────


def test_cli_offers_every_status_the_enum_knows() -> None:
    """Список выводится из TaskStatus — новое состояние появляется само."""
    assert [s.value for s in TaskStatus] == _STATUS_VALUES


def test_canonical_buckets_are_all_reachable() -> None:
    """Семь канонических бакетов + два легаси-алиаса."""
    assert set(_STATUS_VALUES) >= {
        "backlog",
        "todo",
        "in_progress",
        "in_review",
        "blocked",
        "cancelled",
        "done",
    }
    assert {"pending", "in-progress"} <= set(_STATUS_VALUES), "легаси-алиасы не выкидываем"


# ── поведение команд ────────────────────────────────────────────────────────


def test_status_accepts_a_bucket_that_was_unreachable(tmp_path: Path) -> None:
    """`blocked` раньше отвергался самим Choice."""
    project = _seed(tmp_path, {"STT-001": TaskStatus.PENDING})
    result = CliRunner().invoke(main, ["task", "status", "STT-001", "blocked", "-p", project])
    assert result.exit_code == 0, result.output
    assert "blocked" in result.output


def test_checkout_rule_is_not_bypassed_by_the_wider_choice(tmp_path: Path) -> None:
    """ADO-039: `todo → in_progress` по-прежнему только через task_checkout.

    Именно то, что легко сломать, расширяя Choice «в лоб»: значение теперь
    принимается парсером, и запретить переход обязана машина состояний.
    """
    project = _seed(tmp_path, {"STT-002": TaskStatus.PENDING})
    result = CliRunner().invoke(main, ["task", "status", "STT-002", "in_progress", "-p", project])
    assert result.exit_code == 1, result.output
    assert "checkout" in result.output.lower(), (
        "отказ обязан объяснять протокол, а не выглядеть как неизвестный статус"
    )


def test_list_filters_by_a_previously_unreachable_status(tmp_path: Path) -> None:
    """`-s cancelled` — в базе такие строки есть, из CLI их было не достать."""
    project = _seed(
        tmp_path,
        {"STT-003": TaskStatus.PENDING, "STT-004": TaskStatus.CANCELLED},
    )
    result = CliRunner().invoke(main, ["task", "list", "-p", project, "-s", "cancelled", "--json"])
    assert result.exit_code == 0, result.output

    import json

    assert [t["task_id"] for t in json.loads(result.output)] == ["STT-004"]
