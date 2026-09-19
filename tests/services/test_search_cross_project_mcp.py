"""CUR-013 / RFC 22 §3.6: MCP ``ctx_search`` с ``projects`` — hub и отказ.

Тест идёт через настоящий реестр (``COD_DOC_HOME`` подменён autouse-фикстурой),
а не через monkeypatch ``session_factory``: проверяется именно резолв слагов в
одну БД, который и есть предмет задачи.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ADRModel, ProjectModel
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

#: Слаг → adr_id: по одному ADR на проект, оба про «widget».
_HUB_PROJECTS = {"alpha": "ADR-101", "beta": "ADR-202"}


def _get_tool(mcp: FastMCP, name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _ctx_search() -> Any:
    from cod_doc.mcp.tools import doc_tools

    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return _get_tool(mcp, "ctx_search")


def _hub_registry(tmp_path: Path) -> str:
    """Реестр: alpha и beta в одной hub-БД, solo — со своим embedded state.db."""
    hub_url = f"sqlite:///{tmp_path / 'hub.db'}"
    run_alembic("upgrade", "head", db_url=hub_url)

    cfg = Config()
    for name in _HUB_PROJECTS:
        (tmp_path / name).mkdir()
        cfg.add_project(ProjectEntry(name=name, path=str(tmp_path / name), db_url=hub_url))
    (tmp_path / "solo").mkdir()
    cfg.add_project(ProjectEntry(name="solo", path=str(tmp_path / "solo")))
    cfg.save()

    engine = make_engine(hub_url)
    factory = make_session_factory(engine)
    now = datetime.now(UTC)
    with transactional(factory) as session:
        for slug, adr_id in _HUB_PROJECTS.items():
            proj = ProjectModel(
                slug=slug, title=slug, root_path=str(tmp_path / slug), config_json={}
            )
            proj.created = now
            proj.updated = now
            session.add(proj)
            session.flush()
            adr = ADRModel(
                project_id=proj.row_id,
                adr_id=adr_id,
                title=f"{slug} widget decision",
                status="accepted",
                context="ctx",
                decision="widget stays",
            )
            adr.created = now
            adr.last_updated = now
            session.add(adr)
        session.flush()
    engine.dispose()
    return hub_url


def test_ctx_search_projects_merges_hub_projects(tmp_path: Path) -> None:
    """``projects=['beta']`` отдаёт хиты обоих проектов, помеченные слагом."""
    _hub_registry(tmp_path)

    result = _ctx_search()(project="alpha", query="widget", projects=["beta"])

    assert {h["ref"]: h["project"] for h in result["by_kind"]["adr"]} == {
        "ADR-101": "alpha",
        "ADR-202": "beta",
    }
    assert result["meta"]["projects"] == ["alpha", "beta"]
    # ensure_index отработал по каждому проекту набора — оба индекса были пусты.
    assert result["meta"]["index"]["reindexed"] is True
    assert result["meta"]["index_by_project"]["beta"]["reindexed"] is True


def test_ctx_search_without_projects_stays_single(tmp_path: Path) -> None:
    """Без ``projects`` поведение прежнее: только свой проект."""
    _hub_registry(tmp_path)

    result = _ctx_search()(project="alpha", query="widget")

    assert [h["ref"] for h in result["by_kind"]["adr"]] == ["ADR-101"]
    assert "index_by_project" not in result["meta"]


def test_ctx_search_rejects_project_from_another_db(tmp_path: Path) -> None:
    """Проект с другим ``db_url`` — ValueError с внятным текстом, не пустая выдача."""
    _hub_registry(tmp_path)

    with pytest.raises(ValueError, match=r"shared db_url \(hub mode\): solo resolves to"):
        _ctx_search()(project="alpha", query="widget", projects=["solo"])
