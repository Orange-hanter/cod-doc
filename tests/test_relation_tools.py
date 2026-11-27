"""MCP `relate` / `relations` / `relation_*` (RFC 39 §3.7, REL-009)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP

from cod_doc.cli import main
from cod_doc.mcp.tools import relation_tools, story_tools
from cod_doc.services.relation_service import RelationError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _mcp() -> FastMCP:
    mcp = FastMCP("test")
    story_tools.register(mcp)
    relation_tools.register(mcp)
    return mcp


def _t(name: str) -> Callable[..., Any]:
    return _mcp()._tool_manager._tools[name].fn  # type: ignore[no-any-return]


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> str:
    root = tmp_path / "p"
    root.mkdir()
    runner = CliRunner()
    assert runner.invoke(main, ["project", "add", str(root), "--name", "p"]).exit_code == 0
    for sid in ("US-007", "US-064"):
        _t("story_create")(
            project="p", story_id=sid, persona="dev", narrative="n", priority="medium"
        )
    return "p"


def test_relate_returns_canonical_edge_and_is_idempotent(project: str) -> None:
    edge = _t("relate")(
        project=project, from_ref="story:US-064", type="see_also", to_ref="story:US-007"
    )
    assert edge["type"] == "relates_to"
    assert edge["created"] is True
    assert {edge["from"], edge["to"]} == {"story:US-064", "story:US-007"}
    assert edge["edge_uid"]
    again = _t("relate")(
        project=project, from_ref="story:US-007", type="relates_to", to_ref="story:US-064"
    )
    assert again["created"] is False
    assert again["edge_uid"] == edge["edge_uid"]


def test_relations_reads_both_directions(project: str) -> None:
    _t("relate")(project=project, from_ref="story:US-064", type="depends_on", to_ref="story:US-007")
    out = _t("relations")(project=project, ref="story:US-064")
    inc = _t("relations")(project=project, ref="story:US-007")
    assert [(v["label"], v["direction"], v["other"]) for v in out] == [
        ("depends_on", "out", "story:US-007")
    ]
    assert [(v["label"], v["direction"], v["other"]) for v in inc] == [
        ("required_by", "in", "story:US-064")
    ]


def test_error_keeps_message_and_hint(project: str) -> None:
    with pytest.raises(RelationError) as exc:
        _t("relate")(
            project=project, from_ref="story:US-064", type="depend_on", to_ref="story:US-007"
        )
    assert "Похоже: depends_on" in str(exc.value)
    assert exc.value.hint
    assert 'relate(from_ref="story:US-064"' in str(exc.value)


def test_unrelate_and_types_and_define_and_verify(project: str) -> None:
    _t("relate")(project=project, from_ref="story:US-064", type="depends_on", to_ref="story:US-007")
    types = {
        t["name"]: t
        for t in _t("relation_types")(project=project, from_kind="story", to_kind="story")
    }
    assert types["depends_on"]["edges"] == 1
    assert types["depends_on"]["inverse"] == "required_by"
    out = _t("relation_type_define")(
        project=project,
        name="mitigates",
        inverse="mitigated_by",
        from_kinds=["story"],
        to_kinds=["story"],
    )
    assert out["name"] == "mitigates"
    _t("relate")(
        project=project, from_ref="story:US-007", type="mitigated_by", to_ref="story:US-064"
    )
    dep = _t("relation_type_deprecate")(project=project, name="mitigates")
    assert dep["deprecated"] is True
    report = _t("relation_verify")(project=project)
    assert report["checked"] == 0
    assert _t("unrelate")(
        project=project, from_ref="story:US-064", type="depends_on", to_ref="story:US-007"
    ) == {"removed": True}
    assert _t("unrelate")(
        project=project, from_ref="story:US-064", type="depends_on", to_ref="story:US-007"
    ) == {"removed": False}
