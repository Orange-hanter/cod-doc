"""ADO-216: every drift payload names the frontmatter fields that disagree with the DB.

`detect_project_drift` has counted `metadata_mismatch` and listed such a document
in `issues` since ADO-092, but none of the six places that turn a `DriftReport`
into JSON carried the field. On the live cod-doc DB that left 35 documents in
`issues` with `status: in_sync` and no stated reason — the exact blind spot
ADO-092 was built to remove, reintroduced one layer up.

The fixture is the shape the live corpus has: a frontmatter value the enum
cannot hold (`status: resolved`). Import stores the fallback, the file keeps
what it said, the content hashes agree — and only `metadata_mismatch` knows.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP

from cod_doc.cli.doc import doc
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.mcp.tools import doc_tools
from cod_doc.services import curator_service, import_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

UNREPRESENTABLE_STATUS = """---
title: Master
type: guide
status: resolved
owner: human:dakh
---
# Master

Preamble.

## Executive Summary

Summary text.
"""

SLUG = "mm-proj"


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bootstrap_repo(tmp_path: Path) -> tuple[Config, Path]:
    """A repo with one document whose frontmatter status the enum cannot hold."""
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name=SLUG, path=str(repo)).model_dump()]

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        project = ProjectModel(slug=SLUG, title="MM", root_path=str(repo), config_json={})
        project.created = now
        project.updated = now
        session.add(project)
        session.flush()

        report = import_service.import_or_update_markdown(
            session,
            project_id=project.row_id,
            doc_key="master",
            raw_markdown=UNREPRESENTABLE_STATUS,
            author="human:test",
            source_sha256=_sha(UNREPRESENTABLE_STATUS),
            path="master.md",
        )
        model = session.get(DocumentModel, report.document.row_id)
        assert model is not None
        model.path = "master.md"
    engine.dispose()

    (repo / "master.md").write_text(UNREPRESENTABLE_STATUS, encoding="utf-8")
    return cfg, repo


def _cli_json(cfg: Config, *args: str) -> dict[str, Any]:
    result = CliRunner().invoke(
        doc, ["drift", *args, "--project", SLUG, "--json"], obj={"config": cfg}
    )
    assert result.exit_code == 0, result.output
    payload: dict[str, Any] = json.loads(result.output)
    return payload


def _mcp_tool(monkeypatch: pytest.MonkeyPatch, repo: Path, name: str) -> Any:
    factory = make_session_factory(make_engine(f"sqlite:///{repo}/.cod-doc/state.db"))
    entry = SimpleNamespace(path=str(repo))
    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, entry))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: 1)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return mcp._tool_manager._tools[name].fn


def test_cli_single_document_json_names_the_field(tmp_path: Path) -> None:
    cfg, _ = _bootstrap_repo(tmp_path)

    payload = _cli_json(cfg, "master")

    assert payload["metadata_mismatch"] == ["status"]


def test_cli_project_json_issue_names_the_field(tmp_path: Path) -> None:
    cfg, _ = _bootstrap_repo(tmp_path)

    payload = _cli_json(cfg, "--all")

    assert payload["counts"]["metadata_mismatch"] == 1
    (issue,) = payload["issues"]
    assert issue["status"] == "in_sync"
    assert issue["metadata_mismatch"] == ["status"]


@pytest.mark.parametrize("tool", ["doc_drift_all", "ctx_drift"])
def test_mcp_project_drift_issue_names_the_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    _, repo = _bootstrap_repo(tmp_path)

    payload = _mcp_tool(monkeypatch, repo, tool)(project=SLUG)

    (issue,) = payload["issues"]
    assert issue["metadata_mismatch"] == ["status"]


def test_mcp_single_document_drift_names_the_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, repo = _bootstrap_repo(tmp_path)

    payload = _mcp_tool(monkeypatch, repo, "doc_drift")(project=SLUG, doc_key="master")

    assert payload["metadata_mismatch"] == ["status"]


def test_curator_drift_card_issue_names_the_field(tmp_path: Path) -> None:
    _, repo = _bootstrap_repo(tmp_path)
    engine = make_engine(f"sqlite:///{repo}/.cod-doc/state.db")
    try:
        with transactional(make_session_factory(engine)) as session:
            card = curator_service._drift_card(session, 1, repo)
    finally:
        engine.dispose()

    # AFT-002: `in_sync` с расхождением frontmatter в карточке — advisory, не issue.
    assert card["issues"] == []
    assert card["advisory"] == {"count": 1, "doc_keys": ["master"]}
    assert card["counts"]["metadata_mismatch"] == 1
