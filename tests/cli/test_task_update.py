"""ADO-067: CLI-поверхность grooming'а задачи — ``cod-doc task update``.

MCP-эквивалент (`task_update`) и сервис покрыты
tests/services/test_task_update_fields.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    AffectedFileModel,
    PlanModel,
    PlanSectionModel,
    RevisionModel,
    TaskModel,
)
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session


def _init_project(tmp_path: Path, name: str = "gp") -> None:
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


def _seed_task(name: str, task_id: str = "GCL-001") -> None:
    for session in _session(name):
        proj = ProjectRepository(session).get_by_slug(name)
        assert proj is not None and proj.row_id is not None
        now = datetime.now(UTC)
        plan = PlanModel(
            project_id=proj.row_id, scope=f"{name}-plan", created=now, last_updated=now
        )
        session.add(plan)
        session.flush()
        sec = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
        )
        session.add(sec)
        session.flush()
        tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=sec.row_id,
            task_id=task_id,
            title="Task to groom",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
            description="старая формулировка",
            acceptance="старый критерий",
        )


def test_task_update_help_lists_the_three_fields(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["task", "update", "--help"])
    assert result.exit_code == 0, result.output
    for opt in ("--description", "--acceptance", "--priority"):
        assert opt in result.output, f"{opt} отсутствует в `task update --help`"


def test_task_update_changes_fields_and_leaves_trail(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")

    result = CliRunner().invoke(
        main,
        [
            "task",
            "update",
            "GCL-001",
            "--project",
            "gp",
            "--description",
            "новая формулировка",
            "--priority",
            "critical",
            "--reason",
            "грумминг",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "description" in result.output
    assert "priority" in result.output

    for session in _session("gp"):
        t = tasks.get(session, "GCL-001")
        assert t is not None
        assert t.description == "новая формулировка"
        assert t.priority is Priority.CRITICAL
        assert t.acceptance == "старый критерий", "не переданное поле не должно меняться"
        kinds = set(
            session.execute(
                select(ActivityEventModel.kind).where(ActivityEventModel.scope_id == "GCL-001")
            ).scalars()
        )
        assert {"task.description_updated", "task.priority_changed"} <= kinds


def test_task_update_without_fields_exits_nonzero(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")

    result = CliRunner().invoke(main, ["task", "update", "GCL-001", "--project", "gp"])
    assert result.exit_code == 1
    assert "Нечего менять" in result.output


def test_task_update_rejects_unknown_priority(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")

    result = CliRunner().invoke(
        main, ["task", "update", "GCL-001", "--project", "gp", "--priority", "urgent"]
    )
    assert result.exit_code != 0
    assert "urgent" in result.output


def test_task_update_unknown_task_exits_nonzero(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")

    result = CliRunner().invoke(
        main, ["task", "update", "NOPE-999", "--project", "gp", "--priority", "low"]
    )
    assert result.exit_code == 1
    assert "not found" in result.output


# --------------------------------------------------------------------------- #
# AFT-021: --affects-file / --affects-mode / --clear-affects-files            #
# --------------------------------------------------------------------------- #


def _seed_files(name: str, paths: list[str]) -> None:
    for session in _session(name):
        tasks.update_affects_files(
            session, task_id="GCL-001", paths=paths, mode="replace", author="human:seed"
        )


def _files(name: str) -> list[str]:
    for session in _session(name):
        return sorted(
            session.execute(
                select(AffectedFileModel.path)
                .join(TaskModel, TaskModel.row_id == AffectedFileModel.task_id)
                .where(TaskModel.task_id == "GCL-001")
            ).scalars()
        )
    raise AssertionError("unreachable")


def _update(*args: str) -> Result:
    return CliRunner().invoke(main, ["task", "update", "GCL-001", "--project", "gp", *args])


def test_task_update_help_lists_affects_options() -> None:
    result = CliRunner().invoke(main, ["task", "update", "--help"])
    assert result.exit_code == 0, result.output
    for opt in ("--affects-file", "--affects-mode", "--clear-affects-files"):
        assert opt in result.output


def test_task_update_affects_file_replaces_by_default(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")
    _seed_files("gp", ["src/old.py"])

    result = _update("--affects-file", "src/a.py", "--affects-file", "src/a.py")
    assert result.exit_code == 0, result.output
    assert "affects_files" in result.output
    assert _files("gp") == ["src/a.py"]


def test_task_update_affects_mode_add_and_remove(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")
    _seed_files("gp", ["src/a.py"])

    result = _update("--affects-file", "src/b.py", "--affects-mode", "add")
    assert result.exit_code == 0, result.output
    assert _files("gp") == ["src/a.py", "src/b.py"]

    result = _update("--affects-file", "src/a.py", "--affects-mode", "remove")
    assert result.exit_code == 0, result.output
    assert _files("gp") == ["src/b.py"]


def test_task_update_clear_affects_files(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")
    _seed_files("gp", ["src/a.py", "src/b.py"])

    result = _update("--clear-affects-files")
    assert result.exit_code == 0, result.output
    assert _files("gp") == []


def test_task_update_same_set_is_noop(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")
    _seed_files("gp", ["src/a.py"])

    result = _update("--affects-file", "src/a.py")
    assert result.exit_code == 0, result.output
    for session in _session("gp"):
        kinds = list(
            session.execute(
                select(ActivityEventModel.kind).where(
                    ActivityEventModel.kind == "task.affects_files_updated"
                )
            ).scalars()
        )
        assert len(kinds) == 1, "только сид, повтор того же набора — no-op"
        revisions = [
            r
            for r in session.execute(select(RevisionModel.diff)).scalars()
            if json.loads(r).get("op") == "affects_files"
        ]
        assert len(revisions) == 1, "только ревизия сида"
    assert "без изменений" in result.output


def test_task_update_out_of_root_path_warns(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")

    result = _update("--affects-file", "/elsewhere/x.py")
    assert result.exit_code == 0, result.output
    assert "/elsewhere/x.py" in result.output
    assert _files("gp") == ["/elsewhere/x.py"]


def test_task_update_clear_conflicts_with_affects_file(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")

    result = _update("--clear-affects-files", "--affects-file", "src/a.py")
    assert result.exit_code != 0
    assert "--clear-affects-files" in result.output


def test_task_update_affects_mode_without_files_fails(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed_task("gp")

    result = _update("--affects-mode", "add")
    assert result.exit_code != 0
    assert "--affects-file" in result.output
