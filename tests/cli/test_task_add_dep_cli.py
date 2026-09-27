"""ADO-202 (RFC 26 §3.2): CLI `task add-dep` поверх `task_service.add_dependency`.

Семантика upsert и цикла покрыта сервисными тестами; здесь — что команда
доносит op, warnings и отказы до терминала: JSON разбирается, цикл даёт
exit 1 с путём и без трейсбека, --note обязателен. Эталоны — литералы и
прямой SELECT по таблице dependency.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result
from sqlalchemy import text

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "tadc"
_SCOPE = "deps-2026-09"


def _seed(tmp_path: Path, *, done: frozenset[str] = frozenset()) -> None:
    """Проект с планом и тремя задачами TA-001..TA-003 в секции A; ``done`` — закрыты."""
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
            for tid in ("TA-001", "TA-002", "TA-003"):
                task_service.create(
                    session,
                    project_id=project.row_id,
                    plan_id=plan.row_id,
                    section_id=sec.row_id,
                    task_id=tid,
                    title=f"Seed {tid}",
                    type=TaskType.FEATURE,
                    priority=Priority.MEDIUM,
                    author="human:test",
                )
        if done:
            with transactional(factory) as session:
                for tid in done:
                    task_service.complete(session, task_id=tid, author="human:test")
    finally:
        engine.dispose()


def _edges() -> list[tuple[str, str, str, str | None]]:
    """Все рёбра проекта прямым SELECT: (from, to, kind, note)."""
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            rows = session.execute(
                text(
                    "SELECT f.task_id, t.task_id, d.kind, d.note FROM dependency d "
                    "JOIN task f ON f.row_id = d.from_task_id "
                    "JOIN task t ON t.row_id = d.to_task_id "
                    "ORDER BY f.task_id, t.task_id"
                )
            ).all()
    finally:
        engine.dispose()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def _add_dep(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(
        main, ["task", "add-dep", *args, "-p", _PROJECT], env={"FORCE_COLOR": ""}
    )


def test_cli_add_dep_json(tmp_path: Path) -> None:
    """Новое ребро: op=add_dependency, warnings пусты, note лёг в dependency.note."""
    _seed(tmp_path)
    result = _add_dep(["TA-002", "TA-001", "--note", "x", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == {
        "task_id": "TA-002",
        "blocker_id": "TA-001",
        "op": "add_dependency",
        "kind": "blocks",
        "warnings": [],
    }
    assert _edges() == [("TA-002", "TA-001", "blocks", "x")]


def test_cli_add_dep_kind(tmp_path: Path) -> None:
    """--kind доходит до dependency.kind и возвращается в JSON."""
    _seed(tmp_path)
    result = _add_dep(["TA-002", "TA-001", "--note", "x", "--kind", "relates", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["kind"] == "relates"
    assert data["op"] == "add_dependency"
    assert _edges() == [("TA-002", "TA-001", "relates", "x")]


def test_cli_add_dep_kind_default(tmp_path: Path) -> None:
    """Без --kind ребро получает kind 'blocks'."""
    _seed(tmp_path)
    result = _add_dep(["TA-002", "TA-001", "--note", "x", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["kind"] == "blocks"
    assert _edges() == [("TA-002", "TA-001", "blocks", "x")]


def test_cli_add_dep_empty_kind(tmp_path: Path) -> None:
    """Пустой --kind — отказ сервиса сообщением: exit 1, без трейсбека, ребра нет."""
    _seed(tmp_path)
    result = _add_dep(["TA-002", "TA-001", "--note", "x", "--kind", ""])
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert len(_edges()) == 0


def test_cli_add_dep_repeat_and_adopt(tmp_path: Path) -> None:
    """Повтор с тем же note молчит (op None); --adopt пишет adopt_dependency."""
    _seed(tmp_path)
    first = _add_dep(["TA-002", "TA-001", "--note", "x", "--json"])
    assert first.exit_code == 0, first.output

    repeat = _add_dep(["TA-002", "TA-001", "--note", "x", "--json"])
    assert repeat.exit_code == 0, repeat.output
    assert json.loads(repeat.output)["op"] is None

    adopt = _add_dep(["TA-002", "TA-001", "--note", "x", "--adopt", "--json"])
    assert adopt.exit_code == 0, adopt.output
    assert json.loads(adopt.output)["op"] == "adopt_dependency"
    assert _edges() == [("TA-002", "TA-001", "blocks", "x")]


def test_cli_add_dep_cycle(tmp_path: Path) -> None:
    """Ребро, замыкающее цикл, — exit 1, путь в выводе, без трейсбека."""
    _seed(tmp_path)
    for args in (["TA-002", "TA-001"], ["TA-003", "TA-002"]):
        ok = _add_dep([*args, "--note", "chain"])
        assert ok.exit_code == 0, ok.output

    result = _add_dep(["TA-001", "TA-003", "--note", "closes the loop"])
    assert result.exit_code == 1, result.output
    for tid in ("TA-001", "TA-002", "TA-003"):
        assert tid in result.output, result.output
    assert "Traceback" not in result.output
    assert _edges() == [
        ("TA-002", "TA-001", "blocks", "chain"),
        ("TA-003", "TA-002", "blocks", "chain"),
    ]


def test_cli_add_dep_requires_note(tmp_path: Path) -> None:
    """Без --note click отказывает до сервиса, ребро не создаётся."""
    _seed(tmp_path)
    result = _add_dep(["TA-002", "TA-001"])
    assert result.exit_code != 0, result.output
    assert _edges() == []


def test_cli_add_dep_warning_printed(tmp_path: Path) -> None:
    """Закрытый блокер — ребро ставится, предупреждение печатается."""
    _seed(tmp_path, done=frozenset({"TA-001"}))
    result = _add_dep(["TA-002", "TA-001", "--note", "x"])
    assert result.exit_code == 0, result.output
    assert "blocker_closed" in result.output, result.output
    assert _edges() == [("TA-002", "TA-001", "blocks", "x")]
