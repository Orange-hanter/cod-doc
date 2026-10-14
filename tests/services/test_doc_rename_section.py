"""DEBT-008: переименование секции с каскадом якоря — service, MCP, CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP

from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import DocumentStatus, DocumentType, EntityKind
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import link_service
from cod_doc.services import revision_service as rev
from cod_doc.services.doc_service import SectionAlreadyExistsError
from cod_doc.services.validation import ValidationError
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

SOURCE_BODY = (
    "Canonical [[doc:arch/target#data-model]].\n"
    "Relative [model](target.md#data-model).\n"
    "Other anchor [[doc:arch/target#intro]].\n"
    "Decoy [[doc:other#data-model]].\n"
)


def _doc(session: Session, pid: int, key: str, sections: list[tuple[str, str]]) -> int:
    d = docs.create(
        session,
        project_id=pid,
        doc_key=key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        title=key,
        author="human:test",
    )
    assert d.row_id is not None
    for i, (anchor, body) in enumerate(sections):
        docs.add_section(
            session,
            document_id=d.row_id,
            anchor=anchor,
            heading=anchor.replace("-", " ").title(),
            level=2,
            position=i,
            body=body,
            author="human:test",
        )
    return d.row_id


def _seed(session: Session, slug: str = "p", root: str = "/tmp/p") -> tuple[int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=root, config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    pid = proj.row_id
    _doc(session, pid, "other", [("data-model", "Unrelated.")])
    target = _doc(
        session,
        pid,
        "arch/target",
        [("intro", "Jump to [the model](#data-model)."), ("data-model", "Tables.")],
    )
    _doc(session, pid, "arch/source", [("refs", SOURCE_BODY)])
    return pid, target


def _body(session: Session, pid: int, key: str, anchor: str) -> str:
    d = docs.get(session, pid, key)
    assert d is not None
    assert d.row_id is not None
    return next(s.body for s in docs.get_sections(session, d.row_id) if s.anchor == anchor)


def _broken_links(session: Session, pid: int) -> int:
    broken = 0
    for key in ("other", "arch/target", "arch/source"):
        d = docs.get(session, pid, key)
        assert d is not None
        assert d.row_id is not None
        for s in docs.get_sections(session, d.row_id):
            assert s.row_id is not None
            broken += link_service.verify_section(session, s.row_id).broken
    return broken


def test_rename_rewrites_links_and_keeps_section(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, target = _seed(s)
        assert _broken_links(s, pid) == 0
        sec_id = next(x.row_id for x in docs.get_sections(s, target) if x.anchor == "data-model")
        assert sec_id is not None
        history = len(rev.list_for_entity(s, EntityKind.SECTION, sec_id, project_id=pid))

        result = docs.rename_section(
            s, document_id=target, anchor="data-model", heading="Data Model v2", author="t"
        )

        assert (result.section.anchor, result.section.heading) == ("data-model-v2", "Data Model v2")
        assert result.section.row_id == sec_id
        assert result.section.body == "Tables."
        assert len(rev.list_for_entity(s, EntityKind.SECTION, sec_id, project_id=pid)) == history
        assert sorted(result.rewritten) == ["arch/source#refs", "arch/target#intro"]
        assert result.links == 3

        source = _body(s, pid, "arch/source", "refs")
        assert "[[doc:arch/target#data-model-v2]]" in source
        assert "[model](target.md#data-model-v2)" in source
        assert "[[doc:arch/target#intro]]" in source
        assert "[[doc:other#data-model]]" in source
        assert "(#data-model-v2)" in _body(s, pid, "arch/target", "intro")
        assert _broken_links(s, pid) == 0


def test_explicit_anchor_keeps_links_untouched(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, target = _seed(s)
        result = docs.rename_section(
            s,
            document_id=target,
            anchor="data-model",
            heading="Модель данных",
            new_anchor="data-model",
            author="t",
        )
        assert result.section.heading == "Модель данных"
        assert result.rewritten == []
        assert _body(s, pid, "arch/source", "refs") == SOURCE_BODY


def test_collision_empty_heading_and_noop(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, target = _seed(s)
        with pytest.raises(SectionAlreadyExistsError):
            docs.rename_section(
                s, document_id=target, anchor="data-model", heading="Intro", author="t"
            )
        with pytest.raises(ValidationError):
            docs.rename_section(s, document_id=target, anchor="data-model", heading=" ", author="t")
        n = len(rev.list_for_entity(s, EntityKind.DOCUMENT, target, project_id=pid))
        docs.rename_section(
            s, document_id=target, anchor="data-model", heading="Data Model", author="t"
        )
        assert len(rev.list_for_entity(s, EntityKind.DOCUMENT, target, project_id=pid)) == n


def test_mcp_dry_run_reports_without_writing(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import doc_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, _ = _seed(s)
    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    tool: Any = mcp._tool_manager._tools["doc_rename_section"].fn

    out = tool(
        project="p", doc_key="arch/target", anchor="data-model", heading="Schema", dry_run=True
    )

    assert out["to"] == {"anchor": "schema", "heading": "Schema"}
    assert out["links"] == 3
    with transactional(factory) as s:
        assert _body(s, pid, "arch/source", "refs") == SOURCE_BODY


def test_cli_rename_section_json(tmp_path: Path) -> None:
    from cod_doc.cli.doc import doc

    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name="rs", path=str(repo)).model_dump()]
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    with transactional(factory) as s:
        pid, _ = _seed(s, slug="rs", root=str(repo))

    result = CliRunner().invoke(
        doc,
        [
            "rename-section",
            "arch/target",
            "data-model",
            "--heading",
            "Schema",
            "-p",
            "rs",
            "--json",
        ],
        obj={"config": cfg},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["to"]["anchor"] == "schema"
    with transactional(factory) as s:
        assert "[[doc:arch/target#schema]]" in _body(s, pid, "arch/source", "refs")
