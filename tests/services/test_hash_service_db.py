"""DEBT-009: реестр хэшей MASTER.md, заведённый документом, пересчитывается в БД."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import hash_service
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

WRONG = "0123456789ab"
BETA = "Beta body.\n"


def _real_hash() -> str:
    return hashlib.sha256(BETA.encode()).hexdigest()[:12]


def _registry(hash_: str) -> str:
    return f"- 📁 /beta.md | 🗃️ doc:beta_md | 🔑 sha:{hash_}"


def _table(hash_: str) -> str:
    return f"| 1 | Beta | `doc:beta_md` | `{hash_}` | ok |"


def _seed(session: Session, root: Path, *, tracked: bool) -> int:
    (root / "beta.md").write_text(BETA, encoding="utf-8")
    master_text = f"# MASTER\n\n## Links\n\n{_registry(WRONG)}\n\n## Table\n\n{_table(WRONG)}\n"
    (root / "MASTER.md").write_text(master_text, encoding="utf-8")
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path=str(root), config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    if tracked:
        d = docs.create(
            session,
            project_id=proj.row_id,
            doc_key="MASTER",
            type=DocumentType.INDEX,
            status=DocumentStatus.DRAFT,
            title="MASTER",
            path="MASTER.md",
            author="human:test",
        )
        assert d.row_id is not None
        # Строка таблицы — в другой секции, чем блок со ссылкой: пересчёт
        # обязан пройти блоки по всем секциям до таблицы.
        for i, (anchor, body) in enumerate([("links", _registry(WRONG)), ("table", _table(WRONG))]):
            docs.add_section(
                session,
                project_id=proj.row_id,
                document_id=d.row_id,
                anchor=anchor,
                heading=anchor.title(),
                level=2,
                position=i,
                body=body,
                author="human:test",
            )
    return proj.row_id


def _master_body(session: Session, pid: int) -> str:
    d = docs.get_by_path(session, pid, "MASTER.md")
    assert d is not None
    assert d.row_id is not None
    return docs.render_body(session, d.row_id, project_id=pid) or ""


def test_tracked_registry_is_rewritten_in_db_not_on_disk(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s, tmp_path, tracked=True)
        file_before = (tmp_path / "MASTER.md").read_bytes()
        assert [
            f["status"] for f in hash_service.stale_refs(s, pid, tmp_path / "MASTER.md", tmp_path)
        ] == ["STALE"]

        updated, warnings = hash_service.update_master_hashes(
            s, pid, tmp_path / "MASTER.md", author="claude-test", root_path=tmp_path
        )

        assert (updated, warnings) == (2, [])
        body = _master_body(s, pid)
        assert _registry(_real_hash()) in body
        assert _table(_real_hash()) in body
        assert (tmp_path / "MASTER.md").read_bytes() == file_before
        assert hash_service.stale_refs(s, pid, tmp_path / "MASTER.md", tmp_path) == []

        d = docs.get_by_path(s, pid, "MASTER.md")
        assert d is not None
        assert d.row_id is not None
        for sec in docs.get_sections(s, d.row_id, project_id=pid):
            assert sec.row_id is not None
            assert len(rev.list_for_entity(s, EntityKind.SECTION, sec.row_id, project_id=pid)) == 2
        s.flush()
        event = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == hash_service.EVENT_KIND)
        ).one()
        assert event.payload["target"] == "db"


def test_untracked_registry_is_rewritten_on_disk(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s, tmp_path, tracked=False)
        updated, _ = hash_service.update_master_hashes(
            s, pid, tmp_path / "MASTER.md", author="claude-test", root_path=tmp_path
        )
    assert updated == 2
    assert _registry(_real_hash()) in (tmp_path / "MASTER.md").read_text(encoding="utf-8")


def test_mcp_tool_dry_run_then_apply(engine_with_schema, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import repair_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid = _seed(s, tmp_path, tracked=True)

    class _Entry:
        path = str(tmp_path)
        root = tmp_path
        master_path = tmp_path / "MASTER.md"

    monkeypatch.setattr(repair_tools, "session_factory", lambda project: (factory, _Entry()))
    monkeypatch.setattr(repair_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    repair_tools.register(mcp)
    tool: Any = mcp._tool_manager._tools["master_hash_update"].fn

    dry = tool(project="p")
    assert dry["target"] == "db"
    assert [f["status"] for f in dry["stale"]] == ["STALE"]
    assert dry["updated"] == 0

    applied = tool(project="p", dry_run=False, author="claude-test")
    assert applied["updated"] == 2
    with transactional(factory) as s:
        assert WRONG not in _master_body(s, pid)


def test_db_rewrite_is_atomic(engine_with_schema, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Падение на второй секции откатывает и первую: реестр не остаётся наполовину новым."""
    factory = make_session_factory(engine_with_schema)
    real_patch = docs.patch_section
    calls = {"n": 0}

    def _flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real_patch(*args, **kwargs)

    with transactional(factory) as s:
        pid = _seed(s, tmp_path, tracked=True)
    monkeypatch.setattr(docs, "patch_section", _flaky)
    with transactional(factory) as s:
        with pytest.raises(RuntimeError, match="boom"):
            hash_service.update_master_hashes(
                s, pid, tmp_path / "MASTER.md", author="t", root_path=tmp_path
            )
        assert _registry(WRONG) in _master_body(s, pid)
        assert _table(WRONG) in _master_body(s, pid)


def test_mcp_tool_without_registry_reports_instead_of_crashing(  # type: ignore[no-untyped-def]
    engine_with_schema, tmp_path, monkeypatch
) -> None:
    from cod_doc.mcp.tools import repair_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        proj = ProjectModel(slug="p", title="P", root_path=str(tmp_path), config_json={})
        proj.created = proj.updated = datetime.now(UTC)
        s.add(proj)
        s.flush()
        pid = proj.row_id

    class _Entry:
        path = str(tmp_path)
        root = tmp_path
        master_path = tmp_path / "MASTER.md"

    monkeypatch.setattr(repair_tools, "session_factory", lambda project: (factory, _Entry()))
    monkeypatch.setattr(repair_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    repair_tools.register(mcp)
    out = mcp._tool_manager._tools["master_hash_update"].fn(project="p", dry_run=False)
    assert out["updated"] == 0
    assert out["warnings"]
