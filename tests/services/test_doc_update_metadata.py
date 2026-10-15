"""DEBT-004: метаданные документа меняются тулом — service, MCP `doc_update`, CLI `doc update`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    EntityKind,
    Sensitivity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev
from cod_doc.services.validation import ValidationError
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def _add_project(session: Session, slug: str = "p", root: str = "/tmp/p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=root, config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _new_doc(
    session: Session, project_id: int, *, status: DocumentStatus = DocumentStatus.ACTIVE
) -> Document:
    return docs.create(
        session,
        project_id=project_id,
        doc_key="arch/overview",
        type=DocumentType.MODULE_SPEC,
        status=status,
        title="Old title",
        author="human:test",
        owner="human:test",
        sensitivity=Sensitivity.INTERNAL,
        preamble="Old preamble.",
    )


def test_update_changes_only_passed_fields_and_leaves_trace(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        doc = _new_doc(s, pid)
        assert doc.row_id is not None

        result = docs.update_metadata(
            s,
            project_id=pid,
            document_id=doc.row_id,
            author="claude-test",
            title="New title",
            type=DocumentType.ARCHITECTURE,
            sensitivity=Sensitivity.CONFIDENTIAL,
        )

        assert result.document.title == "New title"
        assert result.document.type == DocumentType.ARCHITECTURE
        assert result.document.sensitivity == Sensitivity.CONFIDENTIAL
        assert result.document.owner == "human:test"
        assert result.document.preamble == "Old preamble."
        assert set(result.changes) == {"title", "type", "sensitivity"}
        assert result.changes["title"] == {"from": "Old title", "to": "New title"}

        history = rev.list_for_entity(s, EntityKind.DOCUMENT, doc.row_id, project_id=pid)
        payload = json.loads(history[-1].diff)
        assert payload["op"] == "metadata"
        assert set(payload["changes"]) == {"title", "type", "sensitivity"}

        s.flush()
        events = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.metadata_updated")
        ).all()
        assert len(events) == 1


def test_update_without_difference_writes_nothing(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        doc = _new_doc(s, pid)
        assert doc.row_id is not None
        before = len(rev.list_for_entity(s, EntityKind.DOCUMENT, doc.row_id, project_id=pid))

        result = docs.update_metadata(
            s, project_id=pid, document_id=doc.row_id, author="claude-test", title="Old title"
        )

        assert result.changes == {}
        after = len(rev.list_for_entity(s, EntityKind.DOCUMENT, doc.row_id, project_id=pid))
        assert after == before


def test_clearing_owner_of_active_document_is_refused(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        doc = _new_doc(s, pid)
        assert doc.row_id is not None
        with pytest.raises(ValidationError) as exc:
            docs.update_metadata(
                s, project_id=pid, document_id=doc.row_id, author="claude-test", owner=""
            )
        assert exc.value.code == "FM-002"


def test_clearing_owner_of_draft_document_sets_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        doc = _new_doc(s, pid, status=DocumentStatus.DRAFT)
        assert doc.row_id is not None
        result = docs.update_metadata(
            s, project_id=pid, document_id=doc.row_id, author="claude-test", owner=""
        )
        assert result.document.owner is None


def test_empty_title_is_refused(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        doc = _new_doc(s, pid)
        assert doc.row_id is not None
        with pytest.raises(ValidationError) as exc:
            docs.update_metadata(
                s, project_id=pid, document_id=doc.row_id, author="claude-test", title="  "
            )
        assert exc.value.code == "DM-001"


def _mcp_tool(monkeypatch, factory, proj_id: int) -> Any:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import doc_tools

    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return mcp._tool_manager._tools["doc_update"].fn


def test_mcp_doc_update_dry_run_reports_without_writing(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        _new_doc(s, pid)

    tool = _mcp_tool(monkeypatch, factory, pid)
    out = tool(project="p", doc_key="arch/overview", title="Draft title", dry_run=True)

    assert out["dry_run"] is True
    assert out["changes"]["title"]["to"] == "Draft title"
    with transactional(factory) as s:
        d = docs.get(s, pid, "arch/overview")
        assert d is not None
        assert d.title == "Old title"


def test_mcp_doc_update_writes_and_maps_validation(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        _new_doc(s, pid)

    tool = _mcp_tool(monkeypatch, factory, pid)
    out = tool(project="p", doc_key="arch/overview", preamble="New preamble.", owner="docs")
    assert set(out["changes"]) == {"preamble", "owner"}
    assert out["owner"] == "docs"

    with pytest.raises(ValueError, match="title must not be empty"):
        tool(project="p", doc_key="arch/overview", title="")
    with pytest.raises(ValueError, match="not found"):
        tool(project="p", doc_key="nope", title="X")


def _cli_bootstrap(tmp_path: Path) -> Config:
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name="du", path=str(repo)).model_dump()]
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    with transactional(factory) as s:
        pid = _add_project(s, slug="du", root=str(repo))
        _new_doc(s, pid)
    return cfg


def test_cli_doc_update_json(tmp_path: Path) -> None:
    from cod_doc.cli.doc import doc

    cfg = _cli_bootstrap(tmp_path)
    result = CliRunner().invoke(
        doc,
        ["update", "arch/overview", "-p", "du", "--title", "CLI title", "--json"],
        obj={"config": cfg},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changes"]["title"] == {"from": "Old title", "to": "CLI title"}
    assert payload["dry_run"] is False


def test_cli_doc_update_requires_a_field(tmp_path: Path) -> None:
    from cod_doc.cli.doc import doc

    cfg = _cli_bootstrap(tmp_path)
    result = CliRunner().invoke(doc, ["update", "arch/overview", "-p", "du"], obj={"config": cfg})
    assert result.exit_code == 1


def test_document_of_another_project_is_not_found(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """AFT-024: document_id — row_id на всю hub-БД; чужой проект его не правит."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        other = _add_project(s, slug="q", root="/tmp/q")
        doc = _new_doc(s, pid)
        assert doc.row_id is not None
        with pytest.raises(docs.DocumentNotFoundError):
            docs.update_metadata(s, project_id=other, document_id=doc.row_id, author="t", title="X")


def test_lowering_sensitivity_needs_explicit_declassify(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Понижение грифа снимает редакцию проекций — только с allow_declassify (CLI)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        doc = _new_doc(s, pid)
        assert doc.row_id is not None
        raised = docs.update_metadata(
            s,
            project_id=pid,
            document_id=doc.row_id,
            author="t",
            sensitivity=Sensitivity.RESTRICTED,
        )
        assert raised.document.sensitivity == Sensitivity.RESTRICTED
        with pytest.raises(ValidationError) as exc:
            docs.update_metadata(
                s,
                project_id=pid,
                document_id=doc.row_id,
                author="t",
                sensitivity=Sensitivity.PUBLIC,
            )
        assert exc.value.code == "DM-003"
        lowered = docs.update_metadata(
            s,
            project_id=pid,
            document_id=doc.row_id,
            author="human:test",
            sensitivity=Sensitivity.PUBLIC,
            allow_declassify=True,
        )
        assert lowered.document.sensitivity == Sensitivity.PUBLIC


def test_mcp_cannot_lower_sensitivity(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _add_project(s)
        _new_doc(s, pid)
    tool = _mcp_tool(monkeypatch, factory, pid)
    with pytest.raises(ValueError, match="only a human"):
        tool(project="p", doc_key="arch/overview", sensitivity="public")
