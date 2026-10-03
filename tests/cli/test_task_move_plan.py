"""CLI-поверхность переноса задач между планами — ``cod-doc task move-plan`` (ADO-243).

Сервис и MCP-эквивалент (`task_move_to_plan`) покрыты
tests/services/test_task_move_to_plan.py; здесь — разбор аргументов,
`--from-section`, `--json` и коды выхода, плюс `plan create --id-prefix`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import checkout_service
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session

NAME = "mvpl"


def _init_project(tmp_path: Path) -> None:
    root = tmp_path / NAME
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", NAME])
    assert result.exit_code == 0, result.output


def _session() -> Iterator[Session]:
    entry = Config.load().get_project(NAME)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            yield session
    finally:
        engine.dispose()


def _seed() -> None:
    """Планы `old` (A, B) и `new` (A); ADO-001/002 в old/A, ADO-003 в old/B."""
    for session in _session():
        proj = ProjectRepository(session).get_by_slug(NAME)
        assert proj is not None and proj.row_id is not None
        now = datetime.now(UTC)
        sections: dict[str, int] = {}
        for scope, letters in (("old", "AB"), ("new", "A")):
            plan = PlanModel(project_id=proj.row_id, scope=scope, created=now, last_updated=now)
            session.add(plan)
            session.flush()
            for pos, letter in enumerate(letters):
                sec = PlanSectionModel(
                    plan_id=plan.row_id,
                    letter=letter,
                    title=f"Sec {letter}",
                    slug=f"{letter}-Sec",
                    position=pos,
                )
                session.add(sec)
                session.flush()
                sections[f"{scope}:{letter}"] = sec.row_id
        for task_id, where in (("ADO-001", "old:A"), ("ADO-002", "old:A"), ("ADO-003", "old:B")):
            section = session.get(PlanSectionModel, sections[where])
            assert section is not None
            tasks.create(
                session,
                project_id=proj.row_id,
                plan_id=section.plan_id,
                section_id=sections[where],
                task_id=task_id,
                title=f"Implement: {task_id}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="human:test",
            )


def _scope_of(task_id: str) -> str:
    for session in _session():
        t = tasks.get(session, task_id)
        assert t is not None
        plan = session.get(PlanModel, t.plan_id)
        assert plan is not None
        return plan.scope
    raise AssertionError("unreachable")


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["task", "move-plan", "-p", NAME, *args])
    return result.exit_code, result.output


def test_moves_listed_tasks_and_reports_json(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed()

    code, out = _run("ADO-001", "ADO-002", "--plan", "new", "--section", "A", "--json")
    assert code == 0, out
    payload = json.loads(out)
    assert payload["moved"] == ["ADO-001", "ADO-002"]
    assert payload["plan_scope"] == "new"
    assert payload["committed"] is True
    assert _scope_of("ADO-001") == "new"
    assert _scope_of("ADO-003") == "old"


def test_from_section_moves_the_whole_section(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed()

    code, out = _run("--from-section", "old:B", "--plan", "new", "--section", "A", "--json")
    assert code == 0, out
    assert json.loads(out)["moved"] == ["ADO-003"]
    assert _scope_of("ADO-001") == "old"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed()

    code, out = _run("ADO-001", "--plan", "new", "--section", "A", "--dry-run", "--json")
    assert code == 0, out
    assert json.loads(out)["committed"] is False
    assert _scope_of("ADO-001") == "old"


def test_needs_exactly_one_selector(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed()

    code, out = _run("--plan", "new", "--section", "A")
    assert code == 1
    assert "ровно одно" in out
    code, _ = _run("ADO-001", "--from-section", "old:A", "--plan", "new", "--section", "A")
    assert code == 1


def test_foreign_lock_exits_1_and_moves_nothing(tmp_path: Path) -> None:
    _init_project(tmp_path)
    _seed()
    for session in _session():
        checkout_service.checkout(session, "ADO-002", agent="swarm-kimi")

    code, out = _run("ADO-001", "ADO-002", "--plan", "new", "--section", "A")
    assert code == 1
    assert "swarm-kimi" in out
    assert _scope_of("ADO-001") == "old"


def test_plan_create_takes_id_prefix(tmp_path: Path) -> None:
    _init_project(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "plan",
            "create",
            "web-ui",
            "-p",
            NAME,
            "--principle",
            "track W",
            "--id-prefix",
            "WEB",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id_prefix"] == "WEB"
