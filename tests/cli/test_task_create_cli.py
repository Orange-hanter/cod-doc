"""AFT-011 (RFC 27 F12): CLI `task create` — префикс из плана, занятый ID.

Вывод префикса и проверка занятости покрыты сервисными тестами; здесь — что
CLI больше не требует --id/--prefix, а занятый явный ID приходит понятной
ошибкой с next_free_id, без трейсбека и SQL. Эталоны — литералы.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "tcrc"
_SCOPE = "adoption-2026-08"


def _seed(tmp_path: Path) -> None:
    """Проект с планом adoption-2026-08: секция A, в ней ADO-003."""
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
            plan = PlanModel(project_id=project.row_id, scope=_SCOPE, created=now, last_updated=now)
            session.add(plan)
            session.flush()
            sec = PlanSectionModel(
                plan_id=plan.row_id, letter="A", title="Section A", slug="A-Section-A", position=0
            )
            session.add(sec)
            session.flush()
            task_service.create(
                session,
                project_id=project.row_id,
                plan_id=plan.row_id,
                section_id=sec.row_id,
                task_id="ADO-003",
                title="Seed task",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )
    finally:
        engine.dispose()


def _create(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(
        main,
        [
            "task",
            "create",
            "-p",
            _PROJECT,
            "--plan",
            _SCOPE,
            "--section",
            "A",
            "--title",
            "New task from CLI",
            "--type",
            "feature",
            "--priority",
            "medium",
            *args,
        ],
        env={"FORCE_COLOR": ""},
    )


def test_cli_create_without_id_or_prefix(tmp_path: Path) -> None:
    """Без --id и --prefix префикс выводится из задач плана."""
    _seed(tmp_path)
    result = _create([])
    assert result.exit_code == 0, result.output
    assert "ADO-004" in result.output


def test_cli_create_taken_id(tmp_path: Path) -> None:
    """Занятый явный ID — ошибка с next_free_id, без трейсбека и SQL."""
    _seed(tmp_path)
    result = _create(["--id", "ADO-003"])
    assert result.exit_code == 1, result.output
    assert "ADO-004" in result.output
    for leak in ("Traceback", "INSERT", "UNIQUE constraint", "sqlite3"):
        assert leak not in result.output, result.output


def test_cli_create_explicit_free_id(tmp_path: Path) -> None:
    """Свободный явный ID создаётся как есть."""
    _seed(tmp_path)
    result = _create(["--id", "ADO-010"])
    assert result.exit_code == 0, result.output
    assert "ADO-010" in result.output
