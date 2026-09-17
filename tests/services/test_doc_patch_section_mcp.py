"""MCP tool `doc_patch_section` — edit a section body without a markdown file.

Mirrors the web inline editor's optimistic-concurrency contract
(`cod_doc/api/web/fragments/sections.py::section_patch`) but as an MCP tool
so agents can patch DB-authored documents directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind, Sensitivity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.mcp.tools import doc_tools
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _stub(monkeypatch: pytest.MonkeyPatch, factory: Any, *, require_id: int = 1) -> None:
    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: require_id)


def _add_project(session: Session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id  # type: ignore[return-value]


def _new_doc(session: Session, project_id: int, doc_key: str = "modules/M1-auth/overview") -> int:
    doc = docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="Auth Module Overview",
        author="human:dakh",
        owner="human:dakh",
        sensitivity=Sensitivity.INTERNAL,
        preamble="Intro paragraph.",
    )
    return doc.row_id  # type: ignore[return-value]


def _seed_doc_with_section(factory: Any, *, body: str = "old body") -> tuple[int, int]:
    """Create project + doc + one section; return (doc_row_id, section_row_id)."""
    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc_id = _new_doc(session, proj_id)
        sec = docs.add_section(
            session,
            document_id=doc_id,
            anchor="x",
            heading="X",
            level=2,
            position=0,
            body=body,
            author="human:dakh",
        )
        sec_row_id = sec.row_id
    assert sec_row_id is not None
    return doc_id, sec_row_id


def test_doc_patch_section_success_creates_revision(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id, sec_row_id = _seed_doc_with_section(factory, body="old body")

    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    doc_patch_section = _get_tool(mcp, "doc_patch_section")

    result = doc_patch_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="x",
        body="new body",
        author="human:dakh",
        reason="clarify",
    )

    assert result["changed"] is True
    assert result["doc_key"] == "modules/M1-auth/overview"
    assert result["anchor"] == "x"
    assert result["revision_id"]
    assert "dry_run" not in result

    with transactional(factory) as session:
        history = rev.list_for_entity(session, EntityKind.SECTION, sec_row_id)
        section = next(s for s in docs.get_sections(session, doc_id) if s.anchor == "x")
    # add_section + patch_section == 2 revisions.
    assert len(history) == 2
    assert section.body == "new body"
    assert result["content_hash"] == section.content_hash


def test_doc_patch_section_conflict_on_stale_parent(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id, sec_row_id = _seed_doc_with_section(factory, body="v1")
    with transactional(factory) as session:
        first_rev = rev.list_for_entity(session, EntityKind.SECTION, sec_row_id)[0]

    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    doc_patch_section = _get_tool(mcp, "doc_patch_section")

    # A concurrent writer lands first.
    doc_patch_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="x",
        body="v2",
        author="other",
    )

    # We still think `first_rev` is head — must conflict, not overwrite.
    with pytest.raises(ValueError, match="head moved"):
        doc_patch_section(
            project="p",
            doc_key="modules/M1-auth/overview",
            anchor="x",
            body="v3",
            author="human:dakh",
            expected_parent_revision_id=first_rev.revision_id,
        )

    with transactional(factory) as session:
        section = next(s for s in docs.get_sections(session, doc_id) if s.anchor == "x")
    # v3 must never have landed.
    assert section.body == "v2"


def test_doc_patch_section_dry_run_leaves_db_untouched(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id, sec_row_id = _seed_doc_with_section(factory, body="old body")

    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    doc_patch_section = _get_tool(mcp, "doc_patch_section")

    result = doc_patch_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="x",
        body="new body",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["changed"] is True
    assert "old body" in result["diff"] or "new body" in result["diff"]

    with transactional(factory) as session:
        history = rev.list_for_entity(session, EntityKind.SECTION, sec_row_id)
        section = next(s for s in docs.get_sections(session, doc_id) if s.anchor == "x")
    # Nothing persisted: still just add_section's revision, body unchanged.
    assert len(history) == 1
    assert section.body == "old body"


def test_doc_patch_section_unknown_anchor_errors_cleanly(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    _seed_doc_with_section(factory, body="old body")

    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    doc_patch_section = _get_tool(mcp, "doc_patch_section")

    with pytest.raises(ValueError, match="ghost"):
        doc_patch_section(
            project="p",
            doc_key="modules/M1-auth/overview",
            anchor="ghost",
            body="x",
        )
