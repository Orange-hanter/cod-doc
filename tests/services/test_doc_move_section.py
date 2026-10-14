"""DEBT-007: перестановка секции — service, MCP `doc_move_section`, CLI `doc move-section`."""

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
from cod_doc.services import revision_service as rev
from cod_doc.services.doc_service import SectionNotFoundError
from cod_doc.services.validation import ValidationError
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

ANCHORS = ["a", "b", "c", "d"]


def _seed(session: Session, slug: str = "p", root: str = "/tmp/p") -> tuple[int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=root, config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    d = docs.create(
        session,
        project_id=proj.row_id,
        doc_key="arch/overview",
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        title="Overview",
        author="human:test",
    )
    assert d.row_id is not None
    for i, anchor in enumerate(ANCHORS):
        docs.add_section(
            session,
            project_id=proj.row_id,
            document_id=d.row_id,
            anchor=anchor,
            heading=anchor.upper(),
            level=2,
            position=i,
            body=f"Body {anchor}.",
            author="human:test",
        )
    return proj.row_id, d.row_id


def _order(session: Session, pid: int, doc_id: int) -> list[str]:
    return [
        s.anchor
        for s in sorted(
            docs.get_sections(session, doc_id, project_id=pid), key=lambda s: s.position
        )
    ]


@pytest.mark.parametrize(
    ("anchor", "position", "expected"),
    [
        ("a", 2, ["b", "c", "a", "d"]),
        ("d", 0, ["d", "a", "b", "c"]),
    ],
)
def test_move_reorders_keeps_row_and_history(  # type: ignore[no-untyped-def]
    engine_with_schema, anchor: str, position: int, expected: list[str]
) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, doc_id = _seed(s)
        before = {x.anchor: x.row_id for x in docs.get_sections(s, doc_id, project_id=pid)}
        sec_id = before[anchor]
        history_before = len(rev.list_for_entity(s, EntityKind.SECTION, sec_id, project_id=pid))

        move = docs.move_section(
            s,
            project_id=pid,
            document_id=doc_id,
            anchor=anchor,
            position=position,
            author="claude-test",
        )

        assert move.order == expected
        assert _order(s, pid, doc_id) == expected
        assert sorted(x.position for x in docs.get_sections(s, doc_id, project_id=pid)) == [
            0,
            1,
            2,
            3,
        ]
        assert move.section.row_id == sec_id
        assert len(rev.list_for_entity(s, EntityKind.SECTION, sec_id, project_id=pid)) == (
            history_before
        )
        doc_revs = rev.list_for_entity(s, EntityKind.DOCUMENT, doc_id, project_id=pid)
        assert json.loads(doc_revs[-1].diff)["op"] == "move_section"
        body = docs.render_body(s, doc_id, project_id=pid)
        assert body is not None
        heads = [f"## {a.upper()}" for a in expected]
        assert [body.index(h) for h in heads] == sorted(body.index(h) for h in heads)


def test_move_to_current_position_writes_nothing(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, doc_id = _seed(s)
        n = len(rev.list_for_entity(s, EntityKind.DOCUMENT, doc_id, project_id=pid))
        move = docs.move_section(
            s, project_id=pid, document_id=doc_id, anchor="b", position=1, author="t"
        )
        assert move.from_position == move.to_position == 1
        assert len(rev.list_for_entity(s, EntityKind.DOCUMENT, doc_id, project_id=pid)) == n


def test_out_of_range_and_unknown_anchor(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, doc_id = _seed(s)
        with pytest.raises(ValidationError) as exc:
            docs.move_section(
                s, project_id=pid, document_id=doc_id, anchor="a", position=4, author="t"
            )
        assert exc.value.code == "DM-002"
        with pytest.raises(SectionNotFoundError):
            docs.move_section(
                s, project_id=pid, document_id=doc_id, anchor="zz", position=0, author="t"
            )


def test_mcp_dry_run_reports_order_without_writing(engine_with_schema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools import doc_tools

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, doc_id = _seed(s)
    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: pid)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    tool: Any = mcp._tool_manager._tools["doc_move_section"].fn

    out = tool(project="p", doc_key="arch/overview", anchor="c", position=0, dry_run=True)

    assert out["order"] == ["c", "a", "b", "d"]
    assert out["dry_run"] is True
    with transactional(factory) as s:
        assert _order(s, pid, doc_id) == ANCHORS
    with pytest.raises(ValueError, match="out of range"):
        tool(project="p", doc_key="arch/overview", anchor="c", position=9)


def test_cli_move_section_json(tmp_path: Path) -> None:
    from cod_doc.cli.doc import doc

    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")
    cfg = Config(api_key="sk-test", model="m", base_url="https://x")
    cfg.projects = [ProjectEntry(name="ms", path=str(repo)).model_dump()]
    factory = make_session_factory(make_engine(f"sqlite:///{db_path}"))
    with transactional(factory) as s:
        pid, doc_id = _seed(s, slug="ms", root=str(repo))

    result = CliRunner().invoke(
        doc,
        ["move-section", "arch/overview", "b", "--position", "3", "-p", "ms", "--json"],
        obj={"config": cfg},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["order"] == ["a", "c", "d", "b"]
    with transactional(factory) as s:
        assert _order(s, pid, doc_id) == ["a", "c", "d", "b"]


def test_document_of_another_project_is_not_found(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """AFT-024: чужой проект не переставляет секции документа по его row_id."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        _, doc_id = _seed(s)
        other = ProjectModel(slug="q", title="Q", root_path="/tmp/q", config_json={})
        other.created = other.updated = datetime.now(UTC)
        s.add(other)
        s.flush()
        with pytest.raises(docs.DocumentNotFoundError):
            docs.move_section(
                s, project_id=other.row_id, document_id=doc_id, anchor="a", position=1, author="t"
            )
