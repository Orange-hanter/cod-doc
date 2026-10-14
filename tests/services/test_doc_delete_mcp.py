"""DEBT-006: MCP `doc_delete` — dry run по умолчанию, входящие ссылки в ответе."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _create(session: Session, pid: int, doc_key: str) -> int:
    d = docs.create(
        session,
        project_id=pid,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        title=doc_key,
        author="human:test",
    )
    assert d.row_id is not None
    return d.row_id


def _seed(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    pid = proj.row_id

    target = _create(session, pid, "arch/target")
    docs.add_section(
        session,
        project_id=pid,
        document_id=target,
        anchor="intro",
        heading="Intro",
        level=2,
        position=0,
        body="Target body.",
        author="human:test",
    )
    source = _create(session, pid, "arch/source")
    docs.add_section(
        session,
        project_id=pid,
        document_id=source,
        anchor="refs",
        heading="Refs",
        level=2,
        position=0,
        body="See [[doc:arch/target]] for details.",
        author="human:test",
    )
    return pid


def _tool(monkeypatch, factory, pid: int) -> Any:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import doc_tools

    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return mcp._tool_manager._tools["doc_delete"].fn


def test_default_call_is_dry_run_and_lists_incoming_links(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s)

    out = _tool(monkeypatch, factory, pid)(project="p", doc_key="arch/target")

    assert out["dry_run"] is True
    assert out["deleted"] is False
    assert out["section_count"] == 1
    assert [il["source_doc_key"] for il in out["incoming_links"]] == ["arch/source"]
    assert out["incoming_links"][0]["section_anchor"] == "refs"
    with transactional(factory) as s:
        assert docs.get(s, pid, "arch/target") is not None


def test_explicit_call_deletes_and_emits_event(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s)

    out = _tool(monkeypatch, factory, pid)(
        project="p", doc_key="arch/target", dry_run=False, author="claude-test"
    )

    assert out["deleted"] is True
    with transactional(factory) as s:
        assert docs.get(s, pid, "arch/target") is None
        events = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.deleted")
        ).all()
        assert [e.scope_id for e in events] == ["arch/target"]


def test_unknown_document_raises(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s)
    with pytest.raises(ValueError, match="not found"):
        _tool(monkeypatch, factory, pid)(project="p", doc_key="nope")
