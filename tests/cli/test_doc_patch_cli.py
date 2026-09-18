"""CLI mirror of the MCP section-write tools (STO-011).

`cod-doc doc add-section` / `cod-doc doc patch` must give a human exactly the
interface `doc_add_section` / `doc_patch_section` give an agent: the same
optimistic concurrency, the same no-op semantics, a revision and an activity
event on every write.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner
from sqlalchemy import func, select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind, Sensitivity
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import Result

DOC_KEY = "exp/a"


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _seed_doc(project_name: str = "p", doc_key: str = DOC_KEY) -> None:
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
                type=DocumentType.JOURNAL,
                status=DocumentStatus.ACTIVE,
                title="A",
                author="human:test",
                owner="human:test",
                path="experiments/a.md",
                sensitivity=Sensitivity.INTERNAL,
            )
    finally:
        engine.dispose()


def _sections(project_name: str = "p", doc_key: str = DOC_KEY) -> dict[str, str]:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            d = docs.get(session, proj.row_id, doc_key)
            assert d is not None and d.row_id is not None
            return {s.anchor: s.body for s in docs.get_sections(session, d.row_id)}
    finally:
        engine.dispose()


def _revisions(anchor: str, project_name: str = "p", doc_key: str = DOC_KEY) -> list[str]:
    """Revision ids recorded for the section, oldest first."""
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            d = docs.get(session, proj.row_id, doc_key)
            assert d is not None and d.row_id is not None
            section = next(s for s in docs.get_sections(session, d.row_id) if s.anchor == anchor)
            assert section.row_id is not None
            return [
                r.revision_id
                for r in rev.list_for_entity(session, EntityKind.SECTION, section.row_id)
            ]
    finally:
        engine.dispose()


def _event_count(kind: str, project_name: str = "p") -> int:
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
                        ActivityEventModel.kind == kind,
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _add(runner: CliRunner, *extra: str) -> Result:
    return runner.invoke(
        main,
        ["doc", "add-section", DOC_KEY, "intro", "--project", "p", "--heading", "Intro", *extra],
    )


# --------------------------------------------------------------------------
# add-section
# --------------------------------------------------------------------------


def test_add_section_writes_revision_and_event(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    _seed_doc()

    result = _add(CliRunner(), "--body", "first body")
    assert result.exit_code == 0, result.output
    assert "Added" in result.output

    assert _sections()["intro"] == "first body"
    assert len(_revisions("intro")) == 1
    assert _event_count("doc.section_added") == 1


def test_add_section_body_file_and_default_position(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc()
    body_file = tmp_path / "body.md"
    body_file.write_text("from file\n\nsecond paragraph\n", encoding="utf-8")

    runner = CliRunner()
    assert _add(runner, "--body", "x").exit_code == 0
    result = runner.invoke(
        main,
        [
            "doc",
            "add-section",
            DOC_KEY,
            "details",
            "--project",
            "p",
            "--heading",
            "Details",
            "--body-file",
            str(body_file),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Appended after `intro` (position 0) without renumbering anything.
    assert payload["position"] == 1
    assert payload["created"] is True
    assert _sections()["details"] == "from file\n\nsecond paragraph\n"


def test_add_section_duplicate_anchor_is_rejected(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc()
    runner = CliRunner()
    assert _add(runner, "--body", "first").exit_code == 0

    result = _add(runner, "--body", "second")
    assert result.exit_code != 0
    assert "already exists" in result.output
    # The original body survived.
    assert _sections()["intro"] == "first"


def test_add_section_dry_run_writes_nothing(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    _seed_doc()

    result = _add(CliRunner(), "--body", "preview me", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "preview me" in result.output
    assert _sections() == {}
    assert _event_count("doc.section_added") == 0


# --------------------------------------------------------------------------
# patch
# --------------------------------------------------------------------------


def test_patch_from_stdin_updates_body(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    _seed_doc()
    runner = CliRunner()
    assert _add(runner, "--body", "old body").exit_code == 0

    result = runner.invoke(
        main,
        ["doc", "patch", DOC_KEY, "intro", "--project", "p", "--body-file", "-"],
        input="new body from stdin",
    )
    assert result.exit_code == 0, result.output
    assert "Patched" in result.output
    assert _sections()["intro"] == "new body from stdin"
    assert len(_revisions("intro")) == 2
    assert _event_count("doc.section_updated") == 1


def test_patch_no_op_writes_no_revision(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    _seed_doc()
    runner = CliRunner()
    assert _add(runner, "--body", "same").exit_code == 0

    result = runner.invoke(
        main, ["doc", "patch", DOC_KEY, "intro", "--project", "p", "--body", "same"]
    )
    assert result.exit_code == 0, result.output
    assert "Unchanged" in result.output
    assert len(_revisions("intro")) == 1
    assert _event_count("doc.section_updated") == 0


def test_patch_dry_run_shows_diff_and_writes_nothing(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc()
    runner = CliRunner()
    assert _add(runner, "--body", "old body").exit_code == 0

    result = runner.invoke(
        main,
        ["doc", "patch", DOC_KEY, "intro", "--project", "p", "--body", "new body", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "-old body" in result.output
    assert "+new body" in result.output
    assert _sections()["intro"] == "old body"
    assert len(_revisions("intro")) == 1


def test_patch_stale_expected_revision_conflicts(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc()
    runner = CliRunner()
    assert _add(runner, "--body", "v1").exit_code == 0
    first_rev = _revisions("intro")[0]

    # A concurrent writer lands first.
    assert (
        runner.invoke(
            main, ["doc", "patch", DOC_KEY, "intro", "--project", "p", "--body", "v2"]
        ).exit_code
        == 0
    )

    result = runner.invoke(
        main,
        [
            "doc",
            "patch",
            DOC_KEY,
            "intro",
            "--project",
            "p",
            "--body",
            "v3",
            "--expected-revision",
            first_rev,
        ],
    )
    assert result.exit_code != 0
    assert "Conflict" in result.output
    # v3 never landed.
    assert _sections()["intro"] == "v2"


def test_patch_unknown_anchor_errors_cleanly(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    _seed_doc()

    result = CliRunner().invoke(
        main, ["doc", "patch", DOC_KEY, "ghost", "--project", "p", "--body", "x"]
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_patch_requires_a_body_source(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    _init_project(tmp_path)
    _seed_doc()
    runner = CliRunner()
    assert _add(runner, "--body", "v1").exit_code == 0

    result = runner.invoke(main, ["doc", "patch", DOC_KEY, "intro", "--project", "p"])
    assert result.exit_code != 0
    assert "--body-file" in result.output
