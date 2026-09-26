"""AFT-009 (RFC 27 F10): MCP ``doc_section_get`` и ``head_revision_id`` в ``doc_get``.

Тело секции читается по якорю, несколько якорей — одним вызовом; неизвестный
якорь — структурный miss (PCA-938). ``head_revision_id`` из ``doc_get``
(``include_sections=True``) и ``doc_section_get`` — токен, который
``doc_patch_section`` принимает как ``expected_parent_revision_id``. Эталоны —
литералы сида и ``revision_id`` из ответа ``doc_patch_section``; строить
ожидание вызовом ``read_sections``/``heads_for_entities``/``head_for_entity``/
``get_sections`` запрещено спекой.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from mcp.server.fastmcp import FastMCP

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import doc_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

PROJECT = "pr"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    run_alembic("upgrade", "head", db_url=db_url)
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


def _seed(session: Session) -> int:
    proj = ProjectRepository(session).add(
        ProjectEntity(slug=PROJECT, title=PROJECT, root_path="/tmp/pr", config={})
    )
    assert proj.row_id is not None
    doc = doc_service.create(
        session,
        project_id=proj.row_id,
        doc_key="spec",
        type=DocumentType.GUIDE,
        status=DocumentStatus.DRAFT,
        title="Spec",
        author="seed",
    )
    assert doc.row_id is not None
    for position, (anchor, heading, body) in enumerate(
        [
            ("alpha", "Alpha", "BODY-ALPHA"),
            ("beta", "Beta", "BODY-BETA"),
            ("gamma", "Gamma", "BODY-GAMMA"),
        ]
    ):
        doc_service.add_section(
            session,
            document_id=doc.row_id,
            anchor=anchor,
            heading=heading,
            level=2,
            position=position,
            body=body,
            author="seed",
        )
    return proj.row_id


@pytest.fixture
def tools(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> dict[str, Callable[..., Any]]:
    from cod_doc.mcp.tools import doc_tools

    factory = make_session_factory(engine)
    with transactional(factory) as s:
        proj_id = _seed(s)
    monkeypatch.setattr(doc_tools, "session_factory", lambda project: (factory, None))
    monkeypatch.setattr(doc_tools, "require_project_id", lambda session, project: proj_id)
    mcp = FastMCP("test")
    doc_tools.register(mcp)
    return {
        name: mcp._tool_manager._tools[name].fn
        for name in ("doc_section_get", "doc_get", "doc_patch_section")
    }


def test_section_get_one_and_many(tools: dict[str, Callable[..., Any]]) -> None:
    one = tools["doc_section_get"](project=PROJECT, doc_key="spec", anchors=["beta"])

    assert len(one) == 1
    assert one[0]["anchor"] == "beta"
    assert one[0]["heading"] == "Beta"
    assert one[0]["level"] == 2
    assert one[0]["body"] == "BODY-BETA"
    assert set(one[0]) == {
        "anchor",
        "heading",
        "level",
        "body",
        "content_hash",
        "head_revision_id",
    }

    many = tools["doc_section_get"](project=PROJECT, doc_key="spec", anchors=["gamma", "alpha"])

    assert [(s["anchor"], s["body"]) for s in many] == [
        ("gamma", "BODY-GAMMA"),
        ("alpha", "BODY-ALPHA"),
    ]


def test_section_get_unknown_anchor_miss(tools: dict[str, Callable[..., Any]]) -> None:
    result = tools["doc_section_get"](project=PROJECT, doc_key="spec", anchors=["nope"])

    assert len(result) == 1
    miss = result[0]
    assert miss["anchor"] == "nope"
    assert miss["found"] is False
    assert miss["available_anchors"] == ["alpha", "beta", "gamma"]
    assert miss["related_tools"] == ["doc_get"]
    assert "body" not in miss


def test_section_get_unknown_doc_raises(tools: dict[str, Callable[..., Any]]) -> None:
    with pytest.raises(ValueError, match="Document 'missing' not found"):
        tools["doc_section_get"](project=PROJECT, doc_key="missing", anchors=["alpha"])


def test_doc_get_sections_carry_head_and_hash(tools: dict[str, Callable[..., Any]]) -> None:
    patched = tools["doc_patch_section"](
        project=PROJECT, doc_key="spec", anchor="alpha", body="BODY-ALPHA-2"
    )
    assert patched["changed"] is True

    doc = tools["doc_get"](project=PROJECT, doc_key="spec", include_sections=True)

    assert [s["anchor"] for s in doc["sections"]] == ["alpha", "beta", "gamma"]
    alpha = doc["sections"][0]
    assert alpha["head_revision_id"] == patched["revision_id"]
    assert alpha["content_hash"] == patched["content_hash"]
    for item in doc["sections"]:
        assert set(item) == {
            "anchor",
            "heading",
            "level",
            "position",
            "content_hash",
            "head_revision_id",
        }


def test_head_from_doc_get_accepted_by_patch(tools: dict[str, Callable[..., Any]]) -> None:
    doc = tools["doc_get"](project=PROJECT, doc_key="spec", include_sections=True)
    head = doc["sections"][0]["head_revision_id"]
    assert head is not None

    first = tools["doc_patch_section"](
        project=PROJECT,
        doc_key="spec",
        anchor="alpha",
        body="BODY-ALPHA-2",
        expected_parent_revision_id=head,
    )
    assert first["changed"] is True

    [section] = tools["doc_section_get"](project=PROJECT, doc_key="spec", anchors=["alpha"])
    assert section["body"] == "BODY-ALPHA-2"
    assert section["head_revision_id"] == first["revision_id"]

    second = tools["doc_patch_section"](
        project=PROJECT,
        doc_key="spec",
        anchor="alpha",
        body="BODY-ALPHA-3",
        expected_parent_revision_id=section["head_revision_id"],
    )
    assert second["changed"] is True
    assert second["revision_id"] != first["revision_id"]


def test_stale_head_is_conflict(tools: dict[str, Callable[..., Any]]) -> None:
    [before] = tools["doc_section_get"](project=PROJECT, doc_key="spec", anchors=["beta"])
    stale = before["head_revision_id"]
    assert stale is not None

    other = tools["doc_patch_section"](
        project=PROJECT, doc_key="spec", anchor="beta", body="BODY-BETA-OTHER", author="other"
    )
    assert other["changed"] is True

    with pytest.raises(ValueError):
        tools["doc_patch_section"](
            project=PROJECT,
            doc_key="spec",
            anchor="beta",
            body="BODY-BETA-MINE",
            expected_parent_revision_id=stale,
        )
    [after] = tools["doc_section_get"](project=PROJECT, doc_key="spec", anchors=["beta"])
    assert after["body"] == "BODY-BETA-OTHER"
