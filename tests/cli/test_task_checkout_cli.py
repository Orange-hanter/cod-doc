"""ADO-157: протокол задачи целиком из CLI, без MCP.

ADO-039 сделал `task_checkout` единственным легальным путём из `pending` в
`in-progress`. До этой команды он существовал только как MCP-тул, поэтому
CLI-only сценарий не мог провести задачу по протоколу вообще: `task status`
отказывал, а альтернативы в терминале не было.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session

PROJECT = "co"
TASK = "CO-001"


def _init_project(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    root = tmp_path / PROJECT
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", PROJECT])
    assert result.exit_code == 0, result.output
    return runner


def _session() -> Iterator[Session]:
    entry = Config.load().get_project(PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            yield session
    finally:
        engine.dispose()


def _seed_task() -> None:
    for session in _session():
        proj = ProjectRepository(session).get_by_slug(PROJECT)
        assert proj is not None and proj.row_id is not None
        now = datetime.now(UTC)
        plan = PlanModel(
            project_id=proj.row_id, scope=f"{PROJECT}-plan", created=now, last_updated=now
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
            task_id=TASK,
            title="Protocol check",
            type=TaskType.CHORE,
            priority=Priority.LOW,
            author="human:test",
        )


def _status() -> str:
    for session in _session():
        t = tasks.get(session, TASK)
        assert t is not None
        return str(t.status.value)
    raise AssertionError("нет сессии")


def test_direct_transition_is_refused_and_suggests_the_cli_command(tmp_path: Path) -> None:
    """Отказ приходит из сервиса и называет MCP-тул; CLI дописывает свою команду."""
    runner = _init_project(tmp_path)
    _seed_task()

    r = runner.invoke(main, ["task", "status", TASK, "in-progress", "-p", PROJECT])
    assert r.exit_code == 1
    assert "task_checkout" in r.output
    assert f"cod-doc task checkout {TASK} -p {PROJECT}" in r.output
    assert _status() == "pending"


def test_checkout_moves_pending_to_in_progress(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    _seed_task()

    r = runner.invoke(main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:a"])
    assert r.exit_code == 0, r.output
    assert _status() == "in-progress"


def test_checkout_is_idempotent_for_the_same_agent(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    _seed_task()

    runner.invoke(main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:a"])
    r = runner.invoke(main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:a"])
    assert r.exit_code == 0, r.output
    assert _status() == "in-progress"


def test_checkout_by_another_agent_conflicts(tmp_path: Path) -> None:
    """409: занято, а не гонка — повторять бесполезно."""
    runner = _init_project(tmp_path)
    _seed_task()

    runner.invoke(main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:a"])
    r = runner.invoke(main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:b"])
    assert r.exit_code == 1
    assert "checked out by" in r.output


def test_release_by_another_agent_conflicts_and_hints_force(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    _seed_task()

    runner.invoke(main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:a"])
    r = runner.invoke(main, ["task", "release", TASK, "-p", PROJECT, "--agent", "human:b"])
    assert r.exit_code == 1
    assert "--force" in r.output

    forced = runner.invoke(
        main, ["task", "release", TASK, "-p", PROJECT, "--agent", "human:b", "--force"]
    )
    assert forced.exit_code == 0, forced.output


def test_release_leaves_status_untouched(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    _seed_task()

    runner.invoke(main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:a"])
    r = runner.invoke(main, ["task", "release", TASK, "-p", PROJECT, "--agent", "human:a"])
    assert r.exit_code == 0, r.output
    assert _status() == "in-progress", "release снимает замок, но не откатывает статус"


def test_unknown_task_is_reported(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    _seed_task()

    r = runner.invoke(main, ["task", "checkout", "CO-999", "-p", PROJECT])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_full_protocol_without_mcp(tmp_path: Path) -> None:
    """pending → checkout → in-progress → complete, ни одного MCP-вызова."""
    runner = _init_project(tmp_path)
    _seed_task()

    assert _status() == "pending"
    assert (
        runner.invoke(
            main, ["task", "checkout", TASK, "-p", PROJECT, "--agent", "human:a"]
        ).exit_code
        == 0
    )
    assert _status() == "in-progress"
    done = runner.invoke(main, ["task", "complete", TASK, "-p", PROJECT])
    assert done.exit_code == 0, done.output
    assert _status() == "done"
