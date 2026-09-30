"""ACU-005: рутина `curator_sweep` и выключатели `curator_auto` / `curator_sync`.

Режим берётся из реестра проектов: без `curator_auto` прогон сухой — тот же
план, ни одной записи. `project init` заводит рутину всегда, потому что
выключенный режим безвреден.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import curator_sync_service, projection_service, routine_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = "nightly"
_FM = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[sessionmaker, Path]]:
    root = tmp_path / _PROJECT
    root.mkdir()
    (root / "alpha.md").write_text(f"{_FM}# Alpha\n\n## Details\n\nBody.\n", encoding="utf-8")
    assert (
        CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT]).exit_code == 0
    )
    assert CliRunner().invoke(main, ["import", "docs", _PROJECT]).exit_code == 0
    with (root / "alpha.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка на диске.\n")
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        yield factory, root
    finally:
        engine.dispose()


def _set_flags(**flags: bool) -> None:
    cfg = Config.load()
    for raw in cfg.projects:
        if raw.get("name") == _PROJECT:
            raw.update(flags)
    cfg.save()
    Config.clear_cache()


def _pid(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


def _check(session: Session) -> dict[str, Any]:
    return routine_service.CHECK_CATALOG["curator_sweep"](session, _pid(session))


def _alpha_status(session: Session, root: Path) -> projection_service.DriftStatus:
    from cod_doc.services import doc_service

    doc = doc_service.get(session, _pid(session), "alpha")
    assert doc is not None and doc.row_id is not None
    return projection_service.detect_drift(session, doc.row_id, root_path=root).status


def test_project_init_seeds_the_nightly_routine(project) -> None:
    factory, _root = project
    with transactional(factory, commit=False) as session:
        routine = routine_service.get(session, _pid(session), "curator_sweep_nightly")
    assert routine is not None
    assert routine.check_name == "curator_sweep"
    assert routine.cron == "0 2 * * *"


def test_without_curator_auto_the_routine_only_plans(project) -> None:
    factory, root = project

    with transactional(factory) as session:
        payload = _check(session)

    assert payload["mode"] == "dry_run"
    assert payload["applied_count"] == 0
    assert payload["repair"]["actions"], "план обязан быть посчитан и в сухом режиме"
    with transactional(factory, commit=False) as session:
        assert _alpha_status(session, root) is projection_service.DriftStatus.EDITED_IN_PLACE


def test_with_curator_auto_the_routine_applies(project) -> None:
    factory, root = project
    _set_flags(curator_auto=True)

    with transactional(factory) as session:
        payload = _check(session)

    assert payload["mode"] == "apply"
    assert payload["applied_count"] >= 1
    assert payload["sync_enabled"] is False
    with transactional(factory, commit=False) as session:
        assert _alpha_status(session, root) is projection_service.DriftStatus.IN_SYNC


def test_curator_sync_wires_the_clone_under_cod_doc_home(
    project, monkeypatch: pytest.MonkeyPatch, isolated_cod_doc_home: Path
) -> None:
    """Флаг синхронизации отдаёт прогону выгрузку в клон ~/.cod-doc/curator/<slug>."""
    factory, root = project
    _set_flags(curator_auto=True, curator_sync=True)
    seen: dict[str, Any] = {}

    def _fake_export_sync(
        session: Session, project_id: int, **kwargs: Any
    ) -> curator_sync_service.SyncReport:
        del session, project_id
        seen.update(kwargs)
        return curator_sync_service.SyncReport(branch="curator/sync", base="main", rebuilt=False)

    monkeypatch.setattr(curator_sync_service, "export_sync", _fake_export_sync)
    # Файловый пункт, ради которого прогон вообще зовёт выгрузку.
    (root / "alpha.md").unlink()

    with transactional(factory) as session:
        payload = _check(session)

    assert payload["sync_enabled"] is True
    assert seen["clone_dir"] == isolated_cod_doc_home / "curator" / _PROJECT
    assert seen["repo_root"] == root.resolve()


def test_run_now_records_a_run_with_the_reported_count(project) -> None:
    factory, _root = project
    with transactional(factory) as session:
        run = routine_service.run_now(session, _pid(session), "curator_sweep_nightly")
    assert run.status == "done", run.error
    assert run.findings_count >= 0
