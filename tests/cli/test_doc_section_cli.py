"""CLI mirror of MCP ``doc_section_get`` — ``cod-doc doc section`` (AFT-009)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from pathlib import Path

    from click.testing import Result

SLUG = "p"
DOC_KEY = "spec"


@pytest.fixture
def seeded(tmp_path: Path, isolated_cod_doc_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    root = tmp_path / SLUG
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", SLUG])
    assert result.exit_code == 0, result.output

    entry = Config.load().get_project(SLUG)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            proj = ProjectRepository(session).get_by_slug(SLUG)
            assert proj is not None and proj.row_id is not None
            d = docs.create(
                session,
                project_id=proj.row_id,
                doc_key=DOC_KEY,
                type=DocumentType.JOURNAL,
                status=DocumentStatus.ACTIVE,
                title="Spec",
                author="human:test",
                owner="human:test",
                path="spec.md",
                sensitivity=Sensitivity.INTERNAL,
            )
            assert d.row_id is not None
            for position, (anchor, body) in enumerate(
                [("alpha", "BODY-ALPHA"), ("beta", "BODY-BETA"), ("gamma", "BODY-GAMMA")]
            ):
                docs.add_section(
                    session,
                    document_id=d.row_id,
                    anchor=anchor,
                    heading=anchor.title(),
                    level=2,
                    position=position,
                    body=body,
                    author="human:test",
                )
    finally:
        engine.dispose()


def _run(*args: str) -> Result:
    return CliRunner().invoke(main, ["doc", "section", *args, "-p", SLUG])


def test_doc_section_json_one(seeded: None) -> None:
    result = _run(DOC_KEY, "beta", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["body"] == "BODY-BETA"
    assert set(data[0]) == {
        "anchor",
        "heading",
        "level",
        "body",
        "content_hash",
        "head_revision_id",
    }


def test_doc_section_json_many_order(seeded: None) -> None:
    result = _run(DOC_KEY, "gamma", "alpha", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert [e["anchor"] for e in data] == ["gamma", "alpha"]
    assert [e["body"] for e in data] == ["BODY-GAMMA", "BODY-ALPHA"]


def test_doc_section_miss_json(seeded: None) -> None:
    result = _run(DOC_KEY, "alpha", "nope", "--json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data[0]["body"] == "BODY-ALPHA"
    assert data[1]["anchor"] == "nope"
    assert data[1]["found"] is False
    assert data[1]["available_anchors"] == ["alpha", "beta", "gamma"]


def test_doc_section_human_mode(seeded: None) -> None:
    result = _run(DOC_KEY, "alpha")
    assert result.exit_code == 0, result.output
    assert "BODY-ALPHA" in result.output
    assert "BODY-BETA" not in result.output

    miss = _run(DOC_KEY, "nope")
    assert miss.exit_code == 1, miss.output
    assert "Traceback" not in miss.output
    assert "nope" in miss.output
    assert "beta" in miss.output
    assert "gamma" in miss.output


def test_doc_section_unknown_doc(seeded: None) -> None:
    result = _run("missing/doc", "alpha")
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "not found" in result.output
