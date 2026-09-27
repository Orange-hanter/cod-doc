"""OQM-003: MCP question.* — open questions surface."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.mcp.profiles import keep_tool
from cod_doc.mcp.server import mcp

QUESTION_TOOLS = {
    "question_create",
    "question_get",
    "question_list",
    "question_update",
    "question_resolve",
    "question_drop",
    "question_reopen",
    "question_option_add",
    "question_option_update",
    "question_option_remove",
    "question_link",
    "question_verify",
}


def _tool(name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


@pytest.fixture
def root(tmp_path):  # type: ignore[no-untyped-def]
    root = tmp_path / "p"
    root.mkdir()
    (root / "app.py").write_text("def pay():\n    return 1\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", "p"])
    assert result.exit_code == 0, result.output
    return root


def test_all_question_tools_registered_standard_and_full_only() -> None:
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names >= QUESTION_TOOLS
    for name in QUESTION_TOOLS:
        assert keep_tool(name, "standard"), name
        assert keep_tool(name, "full"), name
        assert not keep_tool(name, "minimal"), name
        assert not keep_tool(name, "agent"), name


def test_create_with_options_and_links_then_get(root) -> None:  # type: ignore[no-untyped-def]
    created = _tool("question_create")(
        project="p",
        title="Провайдер",
        question="Какой?",
        options=[{"title": "A", "body": "дёшево"}, {"title": "B"}],
        links=[{"to_kind": "code", "to_ref": "app.py#pay"}],
        priority="high",
    )
    assert created["question_id"] == "Q-001"
    assert [o["title"] for o in created["options"]] == ["A", "B"]
    assert created["links"][0]["to_ref"] == "app.py#pay"

    got = _tool("question_get")(project="p", question_id="Q-001")
    assert got == created
    assert _tool("question_get")(project="p", question_id="Q-404") is None


def test_lifecycle_through_tools(root) -> None:  # type: ignore[no-untyped-def]
    _tool("question_create")(project="p", title="T", question="Q?")
    _tool("question_option_add")(project="p", question_id="Q-001", title="A")
    _tool("question_option_update")(project="p", question_id="Q-001", position=0, body="b")
    resolved = _tool("question_resolve")(
        project="p", question_id="Q-001", by_adr="ADR-003", chosen_option=0
    )
    assert resolved["status"] == "resolved"
    assert resolved["options"][0]["chosen"] is True
    assert any(e["relation"] == "resolved_by" for e in resolved["links"])

    assert _tool("question_list")(project="p") == []
    listed = _tool("question_list")(project="p", status=None)
    assert [q["question_id"] for q in listed] == ["Q-001"]

    reopened = _tool("question_reopen")(project="p", question_id="Q-001")
    assert reopened["status"] == "open"
    _tool("question_option_remove")(project="p", question_id="Q-001", position=0)
    dropped = _tool("question_drop")(project="p", question_id="Q-001", resolution="снят")
    assert dropped["status"] == "dropped"
    assert dropped["options"] == []


def test_link_reports_target_existence_and_verify(root) -> None:  # type: ignore[no-untyped-def]
    _tool("question_create")(project="p", title="T", question="Q?")
    ok = _tool("question_link")(
        project="p", question_id="Q-001", to_kind="code", to_ref="app.py#L1-L2"
    )
    assert ok["target_exists"] is True
    bad = _tool("question_link")(project="p", question_id="Q-001", to_kind="code", to_ref="gone.py")
    assert bad["target_exists"] is False
    assert "file not found" in bad["broken_reason"]

    report = _tool("question_verify")(project="p")
    assert (report["checked"], report["ok"], report["broken"]) == (2, 1, 1)
    assert report["broken_links"][0]["to_ref"] == "gone.py"

    detached = _tool("question_link")(
        project="p", question_id="Q-001", to_kind="code", to_ref="gone.py", detach=True
    )
    assert detached == {"question_id": "Q-001", "detached": True}
    listed = _tool("question_list")(project="p", linked_kind="code", linked_ref="app.py#L1-L2")
    assert [q["question_id"] for q in listed] == ["Q-001"]


def test_update_clears_owner(root) -> None:  # type: ignore[no-untyped-def]
    _tool("question_create")(project="p", title="T", question="Q?", owner="alice")
    updated = _tool("question_update")(project="p", question_id="Q-001", owner="")
    assert updated["owner"] is None


def test_task_create_addresses_links_question(root) -> None:  # type: ignore[no-untyped-def]
    _tool("question_create")(project="p", title="T", question="Q?")
    _tool("plan_create")(project="p", scope="p-plan", sections=[{"letter": "A", "title": "S"}])
    common = {
        "project": "p",
        "plan_scope": "p-plan",
        "section_letter": "A",
        "type": "feature",
        "priority": "medium",
        "id_prefix": "PP",
    }
    task = _tool("task_create")(**common, title="Answer it", addresses=["Q-001"])
    assert task["addresses"] == ["Q-001"]
    card = _tool("question_get")(project="p", question_id="Q-001")
    assert [(e["to_kind"], e["to_ref"], e["relation"]) for e in card["links"]] == [
        ("task", task["task_id"], "addressed_by")
    ]
    with pytest.raises(ValueError, match="Q-404"):
        _tool("task_create")(**common, title="Other", addresses=["Q-404"])
    # the whole create rolled back: no second task
    tasks = _tool("task_list")(project="p")
    assert [t["title"] for t in tasks["items"]] == ["Answer it"]
