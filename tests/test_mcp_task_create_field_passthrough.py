"""PCA-936: regression guard — MCP `task.create` wrapper passes
``blocked_by`` / ``affects_files`` / ``story_id`` through to ``task_service.create``.

Background: cycle-2 memory ``mcp_field_persistence_gap`` claimed those
three fields were echoed in the response without being persisted to
``dependency`` / ``affected_file`` / ``story_link`` rows. Subsequent
work (PCA-902/903) closed the gap on the service layer; this test
guards the *MCP wrapper* from silently dropping any of them in future
refactors (e.g. someone renames ``affects_files`` → ``affected_files``
at the boundary).

Service-level persistence is covered by
``tests/services/test_task_create.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from cod_doc.mcp.tools import task_tools


def _register_and_get(mcp: FastMCP, name: str) -> Any:
    """Pull the underlying Python callable for a registered tool by name."""
    # FastMCP stores tools in _tool_manager._tools; the wrapped fn is `.fn`.
    return mcp._tool_manager._tools[name].fn


def test_task_create_wrapper_forwards_blocked_by_affects_files_story_id() -> None:
    mcp = FastMCP("test")
    task_tools.register(mcp)
    create = _register_and_get(mcp, "task_create")

    captured: dict[str, Any] = {}

    class _FakeTask:
        row_id = 1
        task_id = "T-001"

    def fake_service_create(session, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return _FakeTask()

    class _FakePlan:
        row_id = 10

    class _FakeSection:
        row_id = 100
        letter = "A"

    with (
        patch("cod_doc.services.task_service.create", side_effect=fake_service_create),
        patch(
            "cod_doc.mcp.tools.task_tools.session_factory",
            return_value=(lambda: None, None),
        ),
        patch(
            "cod_doc.mcp.tools.task_tools.require_project_id",
            return_value=1,
        ),
        patch("cod_doc.infra.db.transactional") as transactional_mock,
        patch("cod_doc.infra.repositories.PlanRepository") as plan_repo_cls,
        patch("cod_doc.infra.repositories.PlanSectionRepository") as section_repo_cls,
        patch(
            "cod_doc.mcp.tools.task_tools.task_to_dict",
            return_value={"task_id": "T-001"},
        ),
    ):
        transactional_mock.return_value.__enter__.return_value = object()
        transactional_mock.return_value.__exit__.return_value = False
        plan_repo_cls.return_value.get_by_scope.return_value = _FakePlan()
        section_repo_cls.return_value.list_for_plan.return_value = [_FakeSection()]

        create(
            project="cod-doc",
            plan_scope="x",
            section_letter="A",
            title="My task",
            type="feature",
            priority="medium",
            id_prefix="T",
            blocked_by=["T-009"],
            affects_files=["a.py", "b.py"],
            story_id="US-1",
        )

    # All three structured fields must reach the service layer with the
    # exact names the service expects. The MCP wrapper renames
    # ``affects_files`` → ``affected_files`` on the way in (because that's
    # the service signature) — this is the easy-to-break boundary.
    assert captured.get("blocked_by") == ["T-009"], (
        f"blocked_by lost in MCP wrapper. Captured kwargs: {sorted(captured)}"
    )
    assert captured.get("affected_files") == ["a.py", "b.py"], (
        f"affects_files → affected_files passthrough broken. Captured kwargs: {sorted(captured)}"
    )
    assert captured.get("story_id") == "US-1", (
        f"story_id lost in MCP wrapper. Captured kwargs: {sorted(captured)}"
    )
