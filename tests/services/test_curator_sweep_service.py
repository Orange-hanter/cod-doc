"""ACU-004: прогон куратора — что делает сам, что отдаёт в клон, что оставляет человеку.

Фикстура — настоящий зарегистрированный проект (`project add` + `import docs`),
как у `test_repair_service`: только так на диске лежат файлы, по которым
считается дрейф. Инвариант каждого сценария — файлы в чекауте владельца
прогон не трогает никогда.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import (
    curator_service,
    curator_sweep_service,
    doc_service,
    doc_tree_service,
    projection_service,
)
from cod_doc.services.curator_sync_service import SyncReport

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = "sweep"
_FM = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[sessionmaker, Path]]:
    root = tmp_path / _PROJECT
    root.mkdir()
    (root / "alpha.md").write_text(f"{_FM}# Alpha\n\n## Details\n\nBody.\n", encoding="utf-8")
    (root / "beta.md").write_text(f"{_FM}# Beta\n\nBeta body.\n", encoding="utf-8")
    added = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert added.exit_code == 0, added.output
    imported = CliRunner().invoke(main, ["import", "docs", _PROJECT])
    assert imported.exit_code == 0, imported.output
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        yield factory, root
    finally:
        engine.dispose()


def _pid(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


def _sweep(session: Session, root: Path, **kwargs: Any) -> curator_sweep_service.SweepReport:
    return curator_sweep_service.sweep(
        session,
        _pid(session),
        root_path=root,
        master_path=root / "MASTER.md",
        slug=_PROJECT,
        **kwargs,
    )


def _status(session: Session, root: Path, doc_key: str) -> projection_service.DriftStatus:
    doc = doc_service.get(session, _pid(session), doc_key)
    assert doc is not None and doc.row_id is not None
    return projection_service.detect_drift(session, doc.row_id, root_path=root).status


def _edit_file(root: Path, name: str, text: str) -> None:
    with (root / name).open("a", encoding="utf-8") as fh:
        fh.write(text)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in root.glob("*.md")}


def _change_db(session: Session, doc_key: str) -> None:
    doc = doc_service.get(session, _pid(session), doc_key)
    assert doc is not None and doc.row_id is not None
    doc_service.add_section(
        session,
        document_id=doc.row_id,
        anchor="db-only",
        heading="DB only",
        level=2,
        position=0,
        body="Правка в БД.\n",
        author="human:test",
    )


@dataclass
class FakeSync:
    calls: int = 0
    exported: list[str] = field(default_factory=list)

    def __call__(self, session: Session, project_id: int) -> SyncReport:
        del session, project_id
        self.calls += 1
        return SyncReport(branch="curator/sync", base="main", rebuilt=False, exported=self.exported)


def test_edited_in_place_is_imported_by_the_curator_and_the_file_is_untouched(project) -> None:
    factory, root = project
    _edit_file(root, "alpha.md", "\nПравка на диске.\n")
    before = _snapshot(root)

    with transactional(factory) as session:
        report = _sweep(session, root)

    assert report.applied_count >= 1
    assert _snapshot(root) == before
    with transactional(factory, commit=False) as session:
        assert _status(session, root, "alpha") is projection_service.DriftStatus.IN_SYNC
        alpha = doc_service.get(session, _pid(session), "alpha")
        assert alpha is not None and alpha.row_id is not None
        assert "Правка на диске." in (doc_service.render_body(session, alpha.row_id) or "")
        authors = set(
            session.execute(
                select(ActivityEventModel.actor_id).where(
                    ActivityEventModel.scope_id == "alpha",
                    ActivityEventModel.kind.like("doc.%"),
                    ActivityEventModel.actor_id.like("agent:%"),
                )
            ).scalars()
        )
        assert authors == {"agent:curator"}


def test_second_sweep_on_a_repaired_project_applies_nothing(project) -> None:
    factory, root = project
    _edit_file(root, "alpha.md", "\nПравка на диске.\n")
    with transactional(factory) as session:
        _sweep(session, root)

    with transactional(factory) as session:
        again = _sweep(session, root)

    assert again.applied_count == 0


def test_conflict_is_reported_and_left_alone(project) -> None:
    factory, root = project
    with transactional(factory) as session:
        _change_db(session, "beta")
    _edit_file(root, "beta.md", "\nПравка на диске.\n")
    before = _snapshot(root)

    with transactional(factory) as session:
        report = _sweep(session, root)

    assert ("drift", "beta") in {(r["kind"], r["ref"]) for r in report.reported}
    assert _snapshot(root) == before
    with transactional(factory, commit=False) as session:
        assert _status(session, root, "beta") is projection_service.DriftStatus.CONFLICT


def test_file_side_items_go_to_the_report_without_sync_and_to_sync_with_it(project) -> None:
    factory, root = project
    with transactional(factory) as session:
        _change_db(session, "beta")
    before = _snapshot(root)

    with transactional(factory) as session:
        without = _sweep(session, root)
    assert ("drift", "beta") in {(r["kind"], r["ref"]) for r in without.reported}

    sync = FakeSync(exported=["beta.md"])
    with transactional(factory) as session:
        with_sync = _sweep(session, root, sync=sync)
    assert sync.calls == 1
    assert ("drift", "beta") not in {(r["kind"], r["ref"]) for r in with_sync.reported}
    assert _snapshot(root) == before


def test_dry_run_writes_nothing(project) -> None:
    factory, root = project
    _edit_file(root, "alpha.md", "\nПравка на диске.\n")
    sync = FakeSync()

    with transactional(factory) as session:
        report = _sweep(session, root, apply=False, sync=sync)

    assert report.applied_count == 0
    assert sync.calls == 0
    with transactional(factory, commit=False) as session:
        assert _status(session, root, "alpha") is projection_service.DriftStatus.EDITED_IN_PLACE


def test_the_cap_leaves_the_whole_group_unapplied(project) -> None:
    factory, root = project
    _edit_file(root, "alpha.md", "\nПравка на диске.\n")

    with transactional(factory) as session:
        report = _sweep(session, root, max_auto=0)

    assert report.applied_count == 0
    assert report.capped
    assert ("drift", "alpha") in {(r["kind"], r["ref"]) for r in report.reported}
    with transactional(factory, commit=False) as session:
        assert _status(session, root, "alpha") is projection_service.DriftStatus.EDITED_IN_PLACE


def test_an_unplaced_document_with_a_matching_rule_is_placed(project) -> None:
    factory, root = project
    (root / "architecture.md").write_text(f"{_FM}# Architecture\n\nLayers.\n", encoding="utf-8")
    assert CliRunner().invoke(main, ["import", "docs", _PROJECT]).exit_code == 0
    with transactional(factory) as session:
        if not doc_tree_service.list_nodes(session, _pid(session)):
            doc_tree_service.init_tree(session, project_id=_pid(session), author="system:init")

    with transactional(factory) as session:
        report = _sweep(session, root)

    assert "architecture" in report.placed
    with transactional(factory, commit=False) as session:
        placed = {
            p.doc_key
            for p in doc_tree_service.classify_project(
                session, project_id=_pid(session), author="t", dry_run=True
            ).placed
        }
    assert "architecture" not in placed


def test_questions_and_unknown_kinds_are_reported_never_acted_on(
    project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Закрыть вопрос — решение человека; незнакомый вид — не повод гадать."""
    factory, root = project
    real_next = curator_service.next

    def _with_extra(*args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = real_next(*args, **kwargs)
        payload["priority"] += [
            {"kind": "question_answered", "ref": "Q-001", "reason": "задачи сделаны"},
            {"kind": "question", "ref": "Q-002", "reason": "открыт 40 дней"},
            {"kind": "alien", "ref": "x", "reason": "новый вид"},
        ]
        return payload

    monkeypatch.setattr(curator_service, "next", _with_extra)
    with transactional(factory) as session:
        report = _sweep(session, root)

    kinds = {r["kind"] for r in report.reported}
    assert {"question_answered", "question", "alien"} <= kinds
