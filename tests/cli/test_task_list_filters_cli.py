"""AFT-006 (RFC 27 F7): CLI `task list` — фильтры по плану, секции, датам.

Сами фильтры покрыты сервисными тестами
(tests/services/test_task_list_filters.py); здесь — что опции CLI доезжают до
сервиса, ошибки приходят ненулевым кодом без трейсбека, а --json-строка несёт
plan_scope / section_letter / completed_at / completed_commit. Канон данных и
литеральные эталоны совпадают с сервисным файлом: ожидания здесь — литералы,
а не пересчёт через сервис.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, TaskModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

_PROJECT = "lstf"

# Канон: какая задача где лежит.
#   plan-x: секция A → PXA-001; секция C → PXC-001..PXC-005
#   plan-y: секция C → PYC-001
_DONE_AT_WITH_COMMIT = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)  # PXC-002, sha abc1234
_DONE_AT_NO_COMMIT_A = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)  # PXC-003, commit None
_DONE_AT_NO_COMMIT_B = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)  # PXC-004, commit ''
_DONE_AT_OLD = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)  # PXC-005, sha def5678

_LAST_UPDATED = {
    "PXA-001": datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
    "PXC-001": datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
    "PXC-002": datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
    "PXC-003": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    "PXC-004": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    "PXC-005": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    "PYC-001": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
}


def _add_section(session: Session, plan_id: int, letter: str, position: int) -> PlanSectionModel:
    sec = PlanSectionModel(
        plan_id=plan_id,
        letter=letter,
        title=f"Section {letter}",
        slug=f"{letter}-Section-{letter}",
        position=position,
    )
    session.add(sec)
    session.flush()
    return sec


def _make(
    session: Session,
    proj: int,
    plan: PlanModel,
    sec: PlanSectionModel,
    tid: str,
    *,
    type: TaskType,
    status: TaskStatus = TaskStatus.TODO,
    completed_at: datetime | None = None,
    completed_commit: str | None = None,
) -> None:
    task = task_service.create(
        session,
        project_id=proj,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=tid,
        title=f"Implement: {tid}",
        type=type,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    assert task.row_id is not None
    model = session.get(TaskModel, task.row_id)
    assert model is not None
    model.status = status.value
    model.completed_at = completed_at
    model.completed_commit = completed_commit
    model.last_updated = _LAST_UPDATED[tid]
    session.flush()


def _seed(tmp_path: Path) -> str:
    """Зарегистрированный проект: plan-x (секции A, C) и plan-y (секция C)."""
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
            proj = project.row_id
            now = datetime.now(UTC)
            plan_x = PlanModel(project_id=proj, scope="plan-x", created=now, last_updated=now)
            plan_y = PlanModel(project_id=proj, scope="plan-y", created=now, last_updated=now)
            session.add_all([plan_x, plan_y])
            session.flush()
            x_a = _add_section(session, plan_x.row_id, "A", 0)
            x_c = _add_section(session, plan_x.row_id, "C", 1)
            y_c = _add_section(session, plan_y.row_id, "C", 0)

            _make(session, proj, plan_x, x_a, "PXA-001", type=TaskType.FEATURE)
            _make(session, proj, plan_x, x_c, "PXC-001", type=TaskType.BUG)
            _make(
                session,
                proj,
                plan_x,
                x_c,
                "PXC-002",
                type=TaskType.BUG,
                status=TaskStatus.DONE,
                completed_at=_DONE_AT_WITH_COMMIT,
                completed_commit="abc1234",
            )
            _make(
                session,
                proj,
                plan_x,
                x_c,
                "PXC-003",
                type=TaskType.CHORE,
                status=TaskStatus.DONE,
                completed_at=_DONE_AT_NO_COMMIT_A,
                completed_commit=None,
            )
            _make(
                session,
                proj,
                plan_x,
                x_c,
                "PXC-004",
                type=TaskType.CHORE,
                status=TaskStatus.DONE,
                completed_at=_DONE_AT_NO_COMMIT_B,
                completed_commit="",
            )
            _make(
                session,
                proj,
                plan_x,
                x_c,
                "PXC-005",
                type=TaskType.DOCS,
                status=TaskStatus.DONE,
                completed_at=_DONE_AT_OLD,
                completed_commit="def5678",
            )
            _make(session, proj, plan_y, y_c, "PYC-001", type=TaskType.BUG)
    finally:
        engine.dispose()
    return _PROJECT


def _run(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(
        main,
        ["task", "list", "-p", _PROJECT, *args],
        env={"FORCE_COLOR": ""},
    )


def _ids(rows: list[dict[str, object]]) -> set[str]:
    return {str(r["task_id"]) for r in rows}


def test_cli_done_recent_with_commit(tmp_path: Path) -> None:
    """«Закрытые за 10 дней с sha» — одна команда (RFC 27 F7)."""
    _seed(tmp_path)
    result = _run(["-s", "done", "--completed-since", "2026-09-16", "--has-commit", "--json"])
    assert result.exit_code == 0, result.output

    rows = json.loads(result.output)
    assert [r["task_id"] for r in rows] == ["PXC-002"]
    row = rows[0]
    assert row["completed_commit"] == "abc1234"
    assert row["completed_at"]
    datetime.fromisoformat(row["completed_at"])


def test_cli_open_tasks_of_section(tmp_path: Path) -> None:
    """«Открытые задачи секции C плана plan-x» — одна команда (RFC 27 F7)."""
    _seed(tmp_path)
    result = _run(["--plan", "plan-x", "--section", "C", "-s", "todo", "--json"])
    assert result.exit_code == 0, result.output

    rows = json.loads(result.output)
    assert _ids(rows) == {"PXC-001"}, "PYC-001 из plan-y обязан отсекаться фильтром плана"
    for row in rows:
        assert row["plan_scope"] == "plan-x"
        assert row["section_letter"] == "C"


def test_cli_section_without_plan_fails(tmp_path: Path) -> None:
    """--section без --plan — понятная ошибка, ненулевой код, без трейсбека."""
    _seed(tmp_path)
    result = _run(["--section", "C"])
    assert result.exit_code != 0
    assert "plan" in result.output
    assert "Traceback" not in result.output


def test_cli_json_row_keys(tmp_path: Path) -> None:
    """Форма --json-строки зафиксирована: прежние поля + четыре новых."""
    _seed(tmp_path)
    result = _run(["--json"])
    assert result.exit_code == 0, result.output

    row = json.loads(result.output)[0]
    assert set(row) == {
        "task_id",
        "title",
        "status",
        "type",
        "priority",
        "plan_scope",
        "section_letter",
        "completed_at",
        "completed_commit",
    }


def test_cli_limit_offset(tmp_path: Path) -> None:
    """Пагинация режет канонический порядок plan/section/task_id."""
    _seed(tmp_path)
    result = _run(["--limit", "2", "--offset", "1", "--json"])
    assert result.exit_code == 0, result.output

    # Полный порядок: PXA-001 (x/A), PXC-001..005 (x/C), PYC-001 (y/C).
    assert [r["task_id"] for r in json.loads(result.output)] == ["PXC-001", "PXC-002"]


def test_cli_type_updated_since_no_commit(tmp_path: Path) -> None:
    """--type, --updated-since и --no-commit дают литеральные множества."""
    _seed(tmp_path)

    result = _run(["--type", "bug", "--json"])
    assert result.exit_code == 0, result.output
    assert _ids(json.loads(result.output)) == {"PXC-001", "PXC-002", "PYC-001"}

    result = _run(["--updated-since", "2026-09-20", "--json"])
    assert result.exit_code == 0, result.output
    assert _ids(json.loads(result.output)) == {"PXC-001", "PXC-002"}

    result = _run(["--no-commit", "--json"])
    assert result.exit_code == 0, result.output
    # NULL и '' считаются «без commit»: из done без sha — PXC-003 и PXC-004.
    assert _ids(json.loads(result.output)) == {
        "PXA-001",
        "PXC-001",
        "PXC-003",
        "PXC-004",
        "PYC-001",
    }
