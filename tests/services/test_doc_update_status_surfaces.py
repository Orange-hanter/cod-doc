"""DEBT-005: смена статуса документа — MCP `doc_update_status` и CLI `doc status`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


def _seed(session: Session, slug: str = "p", root: str = "/tmp/p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=root, config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    docs.create(
        session,
        project_id=proj.row_id,
        doc_key="arch/overview",
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="Overview",
        author="human:test",
        owner="human:test",
    )
    return proj.row_id


def _status_and_revisions(factory: Any, pid: int) -> tuple[DocumentStatus, int]:
    with transactional(factory) as s:
        d = docs.get(s, pid, "arch/overview")
        assert d is not None
        assert d.row_id is not None
        n = len(rev.list_for_entity(s, EntityKind.DOCUMENT, d.row_id, project_id=pid))
        return d.status, n


def _tool(monkeypatch, factory, pid: int) -> Any:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import doc_tools

    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return mcp._tool_manager._tools["doc_update_status"].fn


def test_mcp_deprecates_document_and_writes_revision(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s)
    _, before = _status_and_revisions(factory, pid)

    out = _tool(monkeypatch, factory, pid)(
        project="p", doc_key="arch/overview", status="deprecated", reason="superseded"
    )

    assert (out["from"], out["to"], out["changed"]) == ("active", "deprecated", True)
    status, after = _status_and_revisions(factory, pid)
    assert status == DocumentStatus.DEPRECATED
    assert after == before + 1


def test_mcp_dry_run_and_same_status_write_nothing(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s)
    _, before = _status_and_revisions(factory, pid)
    tool = _tool(monkeypatch, factory, pid)

    dry = tool(project="p", doc_key="arch/overview", status="done", dry_run=True)
    same = tool(project="p", doc_key="arch/overview", status="active")

    assert dry["dry_run"] is True
    assert dry["to"] == "done"
    assert same["changed"] is False
    assert _status_and_revisions(factory, pid) == (DocumentStatus.ACTIVE, before)


def test_mcp_rejects_unknown_status(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s)
    with pytest.raises(ValueError, match="implemented"):
        _tool(monkeypatch, factory, pid)(project="p", doc_key="arch/overview", status="implemented")


def _cli_bootstrap(tmp_path: Path) -> tuple[Config, Any, int]:
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name="ds", path=str(repo)).model_dump()]
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    with transactional(factory) as s:
        pid = _seed(s, slug="ds", root=str(repo))
    return cfg, factory, pid


def test_cli_doc_status_json(tmp_path: Path) -> None:
    from cod_doc.cli.doc import doc

    cfg, factory, pid = _cli_bootstrap(tmp_path)
    result = CliRunner().invoke(
        doc,
        ["status", "arch/overview", "review", "-p", "ds", "--json"],
        obj={"config": cfg},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert (payload["from"], payload["to"], payload["changed"]) == ("active", "review", True)
    assert _status_and_revisions(factory, pid)[0] == DocumentStatus.REVIEW


def test_cli_doc_status_dry_run_leaves_status(tmp_path: Path) -> None:
    from cod_doc.cli.doc import doc

    cfg, factory, pid = _cli_bootstrap(tmp_path)
    result = CliRunner().invoke(
        doc,
        ["status", "arch/overview", "deprecated", "-p", "ds", "--dry-run"],
        obj={"config": cfg},
    )
    assert result.exit_code == 0, result.output
    assert _status_and_revisions(factory, pid)[0] == DocumentStatus.ACTIVE


def test_mcp_status_change_emits_one_event_with_actor(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """ADO-040: событие пишет сервис — ровно одно, с actor_kind из author (ADR-012)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s)

    _tool(monkeypatch, factory, pid)(
        project="p", doc_key="arch/overview", status="deprecated", author="agent:claude-test"
    )

    with transactional(factory) as s:
        events = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "doc.status_changed")
        ).all()
        assert len(events) == 1
        assert (events[0].actor_kind, events[0].actor_id) == ("agent", "agent:claude-test")
        assert events[0].payload["new_status"] == "deprecated"
