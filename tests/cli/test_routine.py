"""CLI tests for ``cod-doc routine`` (ADO-024)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import routine_service

if TYPE_CHECKING:
    from pathlib import Path


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _seed_routine(
    project_name: str,
    *,
    name: str,
    trigger: str = "cron",
    cron: str | None = "*/5 * * * *",
) -> None:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            routine_service.create(
                session,
                proj.row_id,
                name=name,
                check_name="approval_stale",
                trigger=trigger,
                cron=cron,
            )
    finally:
        engine.dispose()


def test_routine_list_empty(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    # `project add` seeds a default approval_stale routine; remove it so the
    # empty-list branch is exercised.
    entry = Config.load().get_project("p")
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug("p")
            assert proj is not None and proj.row_id is not None
            routine_service.delete(session, proj.row_id, "approval_stale_default")
    finally:
        engine.dispose()

    runner = CliRunner()
    result = runner.invoke(main, ["routine", "list", "--project", "p"])
    assert result.exit_code == 0, result.output
    assert "No routines" in result.output


def test_routine_list_shows_routine(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_routine("p", name="nightly-drift")

    runner = CliRunner()
    result = runner.invoke(main, ["routine", "list", "--project", "p"])
    assert result.exit_code == 0, result.output
    assert "nightly-drift" in result.output
    assert "approval_stale" in result.output


def test_routine_tick_fires_due_routine_then_noop(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path, "p")
    _seed_routine("p", name="nightly-drift")

    runner = CliRunner()
    result = runner.invoke(main, ["routine", "tick", "--project", "p"])
    assert result.exit_code == 0, result.output
    assert "nightly-drift" in result.output
    assert "Fired" in result.output

    # Second tick within the 5-minute interval is a no-op (still exit 0).
    result2 = runner.invoke(main, ["routine", "tick", "--project", "p"])
    assert result2.exit_code == 0, result2.output
    assert "Nothing due" in result2.output


def test_routine_run_executes_named_routine(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_routine("p", name="manual-check", trigger="manual", cron=None)

    runner = CliRunner()
    result = runner.invoke(main, ["routine", "run", "manual-check", "--project", "p"])
    assert result.exit_code == 0, result.output
    assert "manual-check" in result.output
    assert "status=done" in result.output


def test_routine_run_unknown_name_errors(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    runner = CliRunner()
    result = runner.invoke(main, ["routine", "run", "no-such", "--project", "p"])
    assert result.exit_code != 0
    assert "not found" in result.output
