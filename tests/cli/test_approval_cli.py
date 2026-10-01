"""ACU-010: `cod-doc approval list|show|approve|deny|cancel`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import approval_service, curator_ops, doc_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = "appr"
_DOC = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n# Guide\n\n## Setup\n\nСм. [гайд](old.md).\n"


@pytest.fixture
def factory(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[sessionmaker]:
    root = tmp_path / _PROJECT
    root.mkdir()
    (root / "guide.md").write_text(_DOC, encoding="utf-8")
    assert (
        CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT]).exit_code == 0
    )
    assert CliRunner().invoke(main, ["import", "docs", _PROJECT]).exit_code == 0
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    sf, engine = db_for_entry(entry)
    try:
        yield sf
    finally:
        engine.dispose()


def _pid(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


def _propose(factory: sessionmaker) -> str:
    args: dict[str, Any] = {"doc_key": "guide", "anchor": "setup", "old": "old.md", "new": "new.md"}
    with transactional(factory) as session:
        pid = _pid(session)
        result = approval_service.request_doc_patch(
            session,
            pid,
            op="link_retarget",
            args=args,
            diff="-[гайд](old.md)\n+[гайд](new.md)\n",
            rationale="old.md переехал в new.md",
            base_revision_id=curator_ops.CURATOR_OPS["link_retarget"].head(session, pid, args),
        )
        assert result.approval is not None
        return result.approval.approval_id


def _run(*args: str) -> Any:
    result = CliRunner().invoke(main, ["approval", *args, "-p", _PROJECT])
    assert result.exit_code == 0, result.output
    return result


def _setup_body(factory: sessionmaker) -> str:
    with transactional(factory, commit=False) as session:
        doc = doc_service.get(session, _pid(session), "guide")
        assert doc is not None and doc.row_id is not None
        return next(
            s.body for s in doc_service.get_sections(session, doc.row_id) if s.anchor == "setup"
        )


def test_list_and_show_carry_the_proposal(factory: sessionmaker) -> None:
    approval_id = _propose(factory)

    listed = json.loads(_run("list", "--json").output)
    assert [i["approval_id"] for i in listed["items"]] == [approval_id]

    shown = _run("show", approval_id[:8]).output
    assert "link_retarget" in shown
    assert "+[гайд](new.md)" in shown
    assert "old.md переехал" in shown


def test_approve_from_the_cli_applies_the_patch(factory: sessionmaker) -> None:
    approval_id = _propose(factory)

    result = json.loads(_run("approve", approval_id[:8], "--by", "human:dakh", "--json").output)

    assert result["approval"]["status"] == "approved"
    assert result["approval"]["resolved_by"] == "human:dakh"
    assert "[гайд](new.md)" in _setup_body(factory)


def test_deny_writes_nothing(factory: sessionmaker) -> None:
    approval_id = _propose(factory)

    _run("deny", approval_id, "--reason", "ссылка верная")

    assert "[гайд](old.md)" in _setup_body(factory)
    listed = json.loads(_run("list", "--status", "denied", "--json").output)
    assert listed["items"][0]["decision_comment"] == "ссылка верная"


def test_approve_of_a_stale_patch_says_so_and_writes_nothing(factory: sessionmaker) -> None:
    approval_id = _propose(factory)
    with transactional(factory) as session:
        doc = doc_service.get(session, _pid(session), "guide")
        assert doc is not None and doc.row_id is not None
        doc_service.patch_section(
            session,
            document_id=doc.row_id,
            anchor="setup",
            new_body="Человек переписал секцию. [гайд](old.md)\n",
            author="human:dakh",
        )

    output = _run("approve", approval_id).output

    assert "устарела" in output
    assert "Человек переписал" in _setup_body(factory)


def test_an_ambiguous_or_unknown_prefix_fails_cleanly(factory: sessionmaker) -> None:
    _propose(factory)

    result = CliRunner().invoke(main, ["approval", "approve", "zzzz", "-p", _PROJECT])

    assert result.exit_code == 1
    assert "найдено 0" in result.output
