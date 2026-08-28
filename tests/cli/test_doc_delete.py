"""CLI tests for ``cod-doc doc delete`` (ADO-031)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import func, select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from pathlib import Path


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _seed_doc(
    project_name: str,
    doc_key: str,
    *,
    path: str,
    doc_type: DocumentType = DocumentType.JOURNAL,
    title: str = "Doc",
) -> None:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            docs.create(
                session,
                project_id=proj.row_id,
                doc_key=doc_key,
                type=doc_type,
                status=DocumentStatus.ACTIVE,
                title=title,
                author="human:test",
                owner="human:test",
                path=path,
                sensitivity=Sensitivity.INTERNAL,
            )
    finally:
        engine.dispose()


def _doc_exists(project_name: str, doc_key: str) -> bool:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            return docs.get(session, proj.row_id, doc_key) is not None
    finally:
        engine.dispose()


def _event_count(project_name: str, doc_key: str) -> int:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            return int(
                session.execute(
                    select(func.count())
                    .select_from(ActivityEventModel)
                    .where(
                        ActivityEventModel.project_id == proj.row_id,
                        ActivityEventModel.kind == "doc.deleted",
                        ActivityEventModel.scope_id == doc_key,
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def test_delete_single_success(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_doc("p", "exp/a", path="experiments/a.md", title="A")

    runner = CliRunner()
    result = runner.invoke(main, ["doc", "delete", "exp/a", "--project", "p"])
    assert result.exit_code == 0, result.output
    assert "Deleted exp/a" in result.output
    assert _doc_exists("p", "exp/a") is False
    assert _event_count("p", "exp/a") == 1


def test_delete_single_unknown_errors(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")

    runner = CliRunner()
    result = runner.invoke(main, ["doc", "delete", "no-such", "--project", "p"])
    assert result.exit_code != 0
    assert "not found" in result.output


def test_delete_single_dry_run_does_not_delete(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_doc("p", "exp/a", path="experiments/a.md", title="A")

    runner = CliRunner()
    result = runner.invoke(main, ["doc", "delete", "exp/a", "--project", "p", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would delete" in result.output
    assert _doc_exists("p", "exp/a") is True
    assert _event_count("p", "exp/a") == 0


def test_delete_bulk_dry_run_lists_candidates(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_doc("p", "exp/a", path="experiments/a.md", title="A")
    _seed_doc("p", "exp/b", path="experiments/b.md", title="B")
    _seed_doc("p", "other", path="other.md", title="Other")

    runner = CliRunner()
    result = runner.invoke(
        main, ["doc", "delete", "--project", "p", "--path-glob", "experiments/**", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "Would delete 2 document(s)" in result.output
    assert "exp/a" in result.output
    assert "exp/b" in result.output
    assert "other" not in result.output
    assert _doc_exists("p", "exp/a") is True


def test_delete_bulk_without_yes_refuses(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_doc("p", "exp/a", path="experiments/a.md", title="A")
    _seed_doc("p", "exp/b", path="experiments/b.md", title="B")

    runner = CliRunner()
    result = runner.invoke(
        main, ["doc", "delete", "--project", "p", "--path-glob", "experiments/**"]
    )
    assert result.exit_code != 0
    assert "Use --yes to delete" in result.output
    assert _doc_exists("p", "exp/a") is True
    assert _doc_exists("p", "exp/b") is True


def test_delete_bulk_with_yes_deletes(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_doc("p", "exp/a", path="experiments/a.md", title="A")
    _seed_doc("p", "exp/b", path="experiments/b.md", title="B")
    _seed_doc("p", "other", path="other.md", title="Other")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["doc", "delete", "--project", "p", "--path-glob", "experiments/**", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "Deleted 2 document(s)" in result.output
    assert _doc_exists("p", "exp/a") is False
    assert _doc_exists("p", "exp/b") is False
    assert _doc_exists("p", "other") is True


def test_delete_bulk_type_filter(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path, "p")
    _seed_doc(
        "p", "exp/journal", path="experiments/journal.md", doc_type=DocumentType.JOURNAL, title="J"
    )
    _seed_doc(
        "p", "exp/design", path="experiments/design.md", doc_type=DocumentType.DESIGN, title="D"
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "doc",
            "delete",
            "--project",
            "p",
            "--path-glob",
            "experiments/**",
            "--type",
            "journal",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Deleted 1 document(s)" in result.output
    assert _doc_exists("p", "exp/journal") is False
    assert _doc_exists("p", "exp/design") is True
