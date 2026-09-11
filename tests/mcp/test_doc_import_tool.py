"""MCP ``doc_import`` — registered path in, unknown path raises."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.mcp.tools import doc_tools
from cod_doc.services import doc_service

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch
    from sqlalchemy.orm import Session, sessionmaker


NOTES_MD = "---\ntype: guide\nstatus: active\n---\n\n# Notes\n\nHello.\n"


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _seed_project(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path=str(root), config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _bind(monkeypatch: MonkeyPatch, factory: sessionmaker[Session], root: Path) -> Any:
    monkeypatch.setattr(
        doc_tools,
        "session_factory",
        lambda project: (factory, SimpleNamespace(path=str(root))),
    )
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return _get_tool(mcp, "doc_import")


def test_doc_import_returns_doc_key(
    engine_with_schema, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "notes.md").write_text(NOTES_MD, encoding="utf-8")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session, root)
        doc_service.create(
            session,
            project_id=pid,
            doc_key="notes",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Notes",
            author="human:test",
            owner="human:test",
            path="notes.md",
            sensitivity=Sensitivity.INTERNAL,
        )

    fn = _bind(monkeypatch, factory, root)
    out = fn(project="p", path="notes.md")
    assert out["doc_key"] == "notes"
    assert out["path"] == "notes.md"


def test_doc_import_unknown_path_raises(
    engine_with_schema, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "other.md").write_text("# x\n", encoding="utf-8")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_project(session, root)

    fn = _bind(monkeypatch, factory, root)
    with pytest.raises(ValueError, match="matches path"):
        fn(project="p", path="other.md")
