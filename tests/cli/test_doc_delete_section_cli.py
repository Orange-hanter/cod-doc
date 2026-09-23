"""CLI mirror of `doc_delete_section` (ADO-213).

`cod-doc doc delete-section` closes the cycle `doc add-section` → `doc patch`
→ this, and the human interface must be the agent's: same revision, same
activity event, same renumbering. It carries one thing the tool does not — a
confirmation prompt, the terminal's own safety, which `--yes` waives for
scripts.
"""

from __future__ import annotations

import json
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

    from click.testing import Result

DOC_KEY = "exp/a"


def _init_project(tmp_path: Path, name: str = "p") -> Path:
    runner = CliRunner()
    root = tmp_path / name
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _seed_doc_with_sections(project_name: str = "p") -> None:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            doc = docs.create(
                session,
                project_id=proj.row_id,
                doc_key=DOC_KEY,
                type=DocumentType.JOURNAL,
                status=DocumentStatus.ACTIVE,
                title="A",
                author="human:test",
                owner="human:test",
                path="experiments/a.md",
                sensitivity=Sensitivity.INTERNAL,
            )
            assert doc.row_id is not None
            for i, anchor in enumerate(("alpha", "beta", "gamma")):
                docs.add_section(
                    session,
                    document_id=doc.row_id,
                    anchor=anchor,
                    heading=anchor.capitalize(),
                    level=2,
                    position=i,
                    body=f"Body of {anchor}.",
                    author="human:test",
                )
    finally:
        engine.dispose()


def _positions(project_name: str = "p") -> list[tuple[str, int]]:
    entry = Config.load().get_project(project_name)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(project_name)
            assert proj is not None and proj.row_id is not None
            d = docs.get(session, proj.row_id, DOC_KEY)
            assert d is not None and d.row_id is not None
            return [(s.anchor, s.position) for s in docs.get_sections(session, d.row_id)]
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


def _delete(runner: CliRunner, anchor: str, *extra: str) -> Result:
    return runner.invoke(main, ["doc", "delete-section", DOC_KEY, anchor, "--project", "p", *extra])


def test_delete_section_removes_row_renumbers_and_leaves_a_trace(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = _delete(CliRunner(), "beta", "--yes")

    assert result.exit_code == 0, result.output
    assert "Deleted" in result.output
    assert _positions() == [("alpha", 0), ("gamma", 1)]
    assert _event_count("doc.section_deleted") == 1


def test_delete_section_asks_before_deleting(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """Without `--yes` a refusal at the prompt must leave the section in place."""
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = CliRunner().invoke(
        main,
        ["doc", "delete-section", DOC_KEY, "beta", "--project", "p"],
        input="n\n",
    )

    assert result.exit_code != 0
    assert [a for a, _ in _positions()] == ["alpha", "beta", "gamma"]
    assert _event_count("doc.section_deleted") == 0


def test_delete_section_json_is_parseable(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """ADO-176: `--json` prints through click.echo, so it parses."""
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = _delete(CliRunner(), "beta", "--json", "--yes")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["anchor"] == "beta"
    assert payload["heading"] == "Beta"
    assert payload["deleted"] is True
    assert payload["remaining_sections"] == 2
    assert payload["revision_id"]


def test_delete_section_dry_run_shows_the_diff_and_writes_nothing(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = _delete(CliRunner(), "beta", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "-Body of beta." in result.output
    assert [a for a, _ in _positions()] == ["alpha", "beta", "gamma"]
    assert _event_count("doc.section_deleted") == 0


def test_delete_section_unknown_anchor_exits_nonzero(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = _delete(CliRunner(), "nope", "--yes")

    assert result.exit_code == 1
    assert "not found" in result.output
    assert len(_positions()) == 3


def test_delete_section_unknown_document_exits_nonzero(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = CliRunner().invoke(
        main, ["doc", "delete-section", "no/such", "alpha", "--project", "p", "--yes"]
    )

    assert result.exit_code == 1
    assert "not found" in result.output


def test_json_is_a_format_not_a_consent(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """ADO-213: `--json` used to imply `--yes` and delete without asking.

    The prompt was skipped under `--json` so the payload would stay parseable.
    That traded the terminal's one safety for tidy output: a machine caller
    says `--yes`, and a human who typed `--json` out of habit still gets asked.
    The question goes to stderr, so stdout keeps parsing.
    """
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = CliRunner().invoke(
        main,
        ["doc", "delete-section", DOC_KEY, "beta", "--project", "p", "--json"],
        input="n\n",
    )

    assert result.exit_code != 0
    assert _positions() == [("alpha", 0), ("beta", 1), ("gamma", 2)]
    assert _event_count("doc.section_deleted") == 0
    assert result.stdout.strip() == ""


def test_json_prompt_answered_yes_still_prints_parseable_json(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    _init_project(tmp_path)
    _seed_doc_with_sections()

    result = CliRunner().invoke(
        main,
        ["doc", "delete-section", DOC_KEY, "beta", "--project", "p", "--json"],
        input="y\n",
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["anchor"] == "beta"
    assert _event_count("doc.section_deleted") == 1
