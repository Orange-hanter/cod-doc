"""MCP tools `doc_patch_section` / `doc_add_section` — author a document body
without a markdown file (STO-010).

`doc_create` stores only the preamble, so before these two tools the only way
into a document's body was the filesystem plus `doc import`. `doc_patch_section`
mirrors the web inline editor's optimistic-concurrency contract
(`cod_doc/api/web/fragments/sections.py::section_patch`); `doc_add_section`
completes the cycle `doc_create` → `doc_add_section` → `doc_patch_section`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind, Sensitivity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel, RevisionModel
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


# ---------------------------------------------------------------------------
# doc_add_section
# ---------------------------------------------------------------------------


def _seed_empty_doc(factory: Any) -> int:
    """Create project + document with no sections; return the doc row id."""
    with transactional(factory) as session:
        proj_id = _add_project(session)
        return _new_doc(session, proj_id)


def _tools(monkeypatch: pytest.MonkeyPatch, factory: Any) -> tuple[Any, Any]:
    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return _get_tool(mcp, "doc_add_section"), _get_tool(mcp, "doc_patch_section")


def test_doc_add_section_creates_section_and_revision(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id = _seed_empty_doc(factory)
    doc_add_section, _ = _tools(monkeypatch, factory)

    result = doc_add_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="intro",
        heading="Intro",
        body="first body",
        author="human:dakh",
    )

    assert result["created"] is True
    assert result["position"] == 0
    assert result["revision_id"]
    assert "dry_run" not in result

    with transactional(factory) as session:
        sections = docs.get_sections(session, doc_id)
        history = rev.list_for_entity(session, EntityKind.SECTION, sections[0].row_id)
    assert [s.anchor for s in sections] == ["intro"]
    assert sections[0].body == "first body"
    assert sections[0].heading == "Intro"
    assert result["content_hash"] == sections[0].content_hash
    assert len(history) == 1


def test_doc_add_section_appends_after_existing(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id, _ = _seed_doc_with_section(factory, body="old body")  # anchor "x" at position 0
    doc_add_section, _ = _tools(monkeypatch, factory)

    result = doc_add_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="y",
        heading="Y",
        body="second",
    )

    assert result["position"] == 1
    with transactional(factory) as session:
        sections = docs.get_sections(session, doc_id)
    assert [(s.anchor, s.position) for s in sections] == [("x", 0), ("y", 1)]


def test_doc_add_section_duplicate_anchor_raises(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id, _ = _seed_doc_with_section(factory, body="old body")
    doc_add_section, _ = _tools(monkeypatch, factory)

    with pytest.raises(ValueError, match="already exists"):
        doc_add_section(
            project="p",
            doc_key="modules/M1-auth/overview",
            anchor="x",
            heading="X again",
            body="clobber",
        )
    # The dry run must reject the same collision instead of previewing a write
    # that could never land.
    with pytest.raises(ValueError, match="already exists"):
        doc_add_section(
            project="p",
            doc_key="modules/M1-auth/overview",
            anchor="x",
            heading="X again",
            body="clobber",
            dry_run=True,
        )

    with transactional(factory) as session:
        sections = docs.get_sections(session, doc_id)
    assert len(sections) == 1
    assert sections[0].body == "old body"


def test_doc_add_section_dry_run_leaves_db_untouched(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id = _seed_empty_doc(factory)
    doc_add_section, _ = _tools(monkeypatch, factory)

    result = doc_add_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="intro",
        heading="Intro",
        body="preview me",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["created"] is False
    assert result["revision_id"] is None
    assert "preview me" in result["diff"]

    with transactional(factory) as session:
        assert docs.get_sections(session, doc_id) == []


def test_doc_authoring_cycle_needs_no_markdown_file(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """STO-010 acceptance: create → add_section → patch_section, all in the DB."""
    factory = make_session_factory(engine_with_schema)
    doc_id = _seed_empty_doc(factory)
    doc_add_section, doc_patch_section = _tools(monkeypatch, factory)

    doc_add_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="intro",
        heading="Intro",
        body="draft",
        author="human:dakh",
    )
    patched = doc_patch_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="intro",
        body="final",
        author="human:dakh",
    )

    assert patched["changed"] is True
    with transactional(factory) as session:
        sections = docs.get_sections(session, doc_id)
        history = rev.list_for_entity(session, EntityKind.SECTION, sections[0].row_id)
        body = docs.render_body(session, doc_id)
    assert sections[0].body == "final"
    assert len(history) == 2
    assert body is not None and "final" in body


# --------------------------------------------------------------------------- #
# ADO-213: doc_delete_section — the third of the cycle                         #
# --------------------------------------------------------------------------- #


def _delete_tool(monkeypatch: pytest.MonkeyPatch, factory: Any) -> Any:
    _stub(monkeypatch, factory)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return _get_tool(mcp, "doc_delete_section")


def test_doc_delete_section_removes_the_section(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id, _ = _seed_doc_with_section(factory, body="drop me")
    doc_delete_section = _delete_tool(monkeypatch, factory)

    result = doc_delete_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="x",
        author="human:dakh",
        reason="left the file",
    )

    assert result["deleted"] is True
    assert result["anchor"] == "x"
    assert result["heading"] == "X"
    assert result["position"] == 0
    assert result["remaining_sections"] == 0
    assert result["revision_id"]
    assert "dry_run" not in result

    with transactional(factory) as session:
        assert docs.get_sections(session, doc_id) == []
        # ai-review #85 (critical): тул искал ревизию по SECTION и возвращал
        # прошлую правку уже удалённой секции — непустой id, но не тот.
        # Ревизия удаления — на документе.
        model = session.execute(
            select(RevisionModel).where(RevisionModel.revision_id == result["revision_id"])
        ).scalar_one()
        assert (model.entity_kind, model.entity_id) == (EntityKind.DOCUMENT.value, doc_id)
        assert json.loads(model.diff)["op"] == "delete_section"


def test_doc_delete_section_dry_run_leaves_db_untouched(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    doc_id, _ = _seed_doc_with_section(factory, body="keep me")
    doc_delete_section = _delete_tool(monkeypatch, factory)

    result = doc_delete_section(
        project="p",
        doc_key="modules/M1-auth/overview",
        anchor="x",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["deleted"] is False
    assert result["revision_id"] is None
    assert result["remaining_sections"] == 0
    assert "-keep me" in result["diff"]

    with transactional(factory) as session:
        assert [s.anchor for s in docs.get_sections(session, doc_id)] == ["x"]


def test_doc_delete_section_rejects_unknown_anchor(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    _seed_doc_with_section(factory)
    doc_delete_section = _delete_tool(monkeypatch, factory)

    with pytest.raises(ValueError, match="Section 'nope' not found"):
        doc_delete_section(project="p", doc_key="modules/M1-auth/overview", anchor="nope")


def test_doc_delete_section_rejects_unknown_document(
    engine_with_schema, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    _seed_doc_with_section(factory)
    doc_delete_section = _delete_tool(monkeypatch, factory)

    with pytest.raises(ValueError, match="Document 'nope' not found"):
        doc_delete_section(project="p", doc_key="nope", anchor="x")
