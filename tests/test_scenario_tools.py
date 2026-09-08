"""TSC-007: MCP scenario.* — the authoring surface of RFC 24 §9."""

from __future__ import annotations

import asyncio
from typing import Any

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.mcp.profiles import keep_tool
from cod_doc.mcp.server import mcp
from cod_doc.services.projection_service import export as export_mod

SCENARIO_TOOLS = {
    "scenario_create",
    "scenario_get",
    "scenario_list",
    "scenario_update",
    "scenario_retire",
    "scenario_set_steps",
    "scenario_link",
    "scenario_export",
    "scenario_coverage",
}


def _tool(name: str) -> Any:
    return mcp._tool_manager._tools[name].fn


def _registered_names() -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def _init_project(tmp_path, name: str = "p"):  # type: ignore[no-untyped-def]
    root = tmp_path / name
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", name])
    assert result.exit_code == 0, result.output
    return root


def _create(**kw: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "title": "Plan progress recomputes after a task completes",
        "kind": "happy_path",
        "preconditions": "A plan with one open task exists.",
        "expected": "plan_progress reports one task done.",
        "steps": ["Complete the task", "Read the plan progress"],
        "project": "p",
        "doc_key": "docs/system/capabilities/plan-management",
    }
    params.update(kw)
    return _tool("scenario_create")(**params)


# --------------------------------------------------------------------------- #
# registration + profiles                                                      #
# --------------------------------------------------------------------------- #


def test_all_scenario_tools_are_registered() -> None:
    assert _registered_names() >= SCENARIO_TOOLS


def test_scenario_tools_are_standard_and_full_only() -> None:
    """`agent` and `minimal` are explicit allowlists — the names must not leak."""
    for name in SCENARIO_TOOLS:
        assert keep_tool(name, "standard"), name
        assert keep_tool(name, "full"), name
        assert not keep_tool(name, "minimal"), name
        assert not keep_tool(name, "agent"), name


def test_no_structure_name_is_claimed() -> None:
    """RFC 24 §14 reserves structure_* for the evidence surface (STR-004)."""
    assert not {n for n in _registered_names() if n.startswith("structure_")}


# --------------------------------------------------------------------------- #
# round trip                                                                   #
# --------------------------------------------------------------------------- #


def test_create_get_list_round_trip(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    _init_project(tmp_path)
    created = _create()
    assert created["scenario_id"] == "SCN-001"
    assert created["group_key"] == "plan-management"
    assert created["status"] == "draft"

    fetched = _tool("scenario_get")(scenario_id="SCN-001", project="p")
    assert fetched["steps"] == ["Complete the task", "Read the plan progress"]

    listed = _tool("scenario_list")(project="p")
    assert [s["scenario_id"] for s in listed] == ["SCN-001"]


def test_get_unknown_returns_none(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    _init_project(tmp_path)
    assert _tool("scenario_get")(scenario_id="SCN-404", project="p") is None


def test_set_steps_replaces(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    _init_project(tmp_path)
    _create()
    steps = _tool("scenario_set_steps")(scenario_id="SCN-001", steps=["Only one"], project="p")
    assert steps == ["Only one"]


def test_link_and_detach(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    _init_project(tmp_path)
    _create()
    args = {
        "scenario_id": "SCN-001",
        "to_kind": "criterion",
        "to_ref": "US-013#2",
        "relation": "verifies",
        "project": "p",
    }
    assert _tool("scenario_link")(**args)["attached"] is True
    assert _tool("scenario_get")(scenario_id="SCN-001", project="p")["links"][0]["to_ref"] == (
        "US-013#2"
    )
    assert _tool("scenario_link")(**args, detach=True)["detached"] is True
    assert _tool("scenario_get")(scenario_id="SCN-001", project="p")["links"] == []


def test_retire_then_coverage(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    _init_project(tmp_path)
    _create()
    _create(kind="error_path", title="Second")
    retired = _tool("scenario_retire")(scenario_id="SCN-001", project="p")
    assert retired["status"] == "retired"

    coverage = _tool("scenario_coverage")(project="p")
    assert coverage[0]["total"] == 1
    assert coverage[0]["retired"] == 1
    assert coverage[0]["missing_kinds"] == ["happy_path"]


# --------------------------------------------------------------------------- #
# the load-bearing refusal                                                     #
# --------------------------------------------------------------------------- #


def test_coverage_verdict_is_refused_as_status(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    """A §9 verdict is producer evidence and must not be settable over MCP."""
    import pytest

    from cod_doc.services.validation import ValidationError

    _init_project(tmp_path)
    _create()
    with pytest.raises(ValidationError) as exc:
        _tool("scenario_update")(scenario_id="SCN-001", status="covered", project="p")
    assert exc.value.code == "SCV-003"


def test_coverage_payload_carries_no_verdicts(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    _init_project(tmp_path)
    _create()
    payload = _tool("scenario_coverage")(project="p")[0]
    for verdict in ("covered", "partial", "missing", "unverifiable"):
        assert verdict not in payload


# --------------------------------------------------------------------------- #
# export                                                                       #
# --------------------------------------------------------------------------- #


def test_export_refuses_a_foreign_checkout(tmp_path, isolated_cod_doc_home) -> None:  # type: ignore[no-untyped-def]
    """Same ADO-010 speed bump `doc_export` has: exporting into someone else's
    repository must be asked for explicitly."""
    import pytest

    from cod_doc.services.projection_service import ExportGuardError

    _init_project(tmp_path)
    _create()
    with pytest.raises(ExportGuardError, match="own"):
        _tool("scenario_export")(project="p", group_key="plan-management")


def test_export_writes_the_projection(tmp_path, isolated_cod_doc_home, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    root = _init_project(tmp_path)
    monkeypatch.setattr(export_mod, "_own_source_checkout", lambda: None)
    _create()
    results = _tool("scenario_export")(project="p", group_key="plan-management")
    assert results[0]["written"] is True

    target = root / "docs" / "system" / "scenarios" / "plan-management.md"
    assert target.exists()
    body = target.read_text()
    assert "## SCN-001" in body
    assert "generated by scenario_service" in body


def test_export_is_idempotent(tmp_path, isolated_cod_doc_home, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _init_project(tmp_path)
    monkeypatch.setattr(export_mod, "_own_source_checkout", lambda: None)
    _create()
    _tool("scenario_export")(project="p", group_key="plan-management")
    again = _tool("scenario_export")(project="p", group_key="plan-management")
    assert again[0]["written"] is False
