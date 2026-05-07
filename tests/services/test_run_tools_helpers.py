"""PCA-032: run_tools query helpers (list_runs_for_project + get_run_with_mutations)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import AuditLogModel, ProjectModel
from cod_doc.mcp.tools.run_tools import (
    get_run_with_mutations,
    list_runs_for_project,
)
from cod_doc.services import revision_service as rev
from cod_doc.services.run_context import run_scope

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_project(session: Session, slug: str = "rt") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(
        slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={}
    )
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


# --------------------------------------------------------------------------- #
# list_runs_for_project                                                        #
# --------------------------------------------------------------------------- #


def test_list_runs_empty(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _add_project(session)
        result = list_runs_for_project(session, project_id)
    assert result == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_runs_returns_newest_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        for i in range(3):
            with run_scope(session, project_id=pid, run_id=f"r-{i}", wake_reason="manual"):
                pass

    with transactional(factory) as session:
        result = list_runs_for_project(session, pid)
    assert result["total"] == 3
    ids = [item["run_id"] for item in result["items"]]
    # row_id-tiebreak gives newest-first as r-2, r-1, r-0
    assert ids[0] == "r-2"
    assert ids[-1] == "r-0"


def test_list_runs_status_filter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    import pytest as _pytest

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        # One done, one failed.
        with run_scope(session, project_id=pid, run_id="ok-1"):
            pass
        with (
            _pytest.raises(RuntimeError),
            run_scope(session, project_id=pid, run_id="bad-1"),
        ):
            raise RuntimeError("boom")

    with transactional(factory) as session:
        done = list_runs_for_project(session, pid, status="done")
        failed = list_runs_for_project(session, pid, status="failed")

    assert done["total"] == 1 and done["items"][0]["run_id"] == "ok-1"
    assert failed["total"] == 1 and failed["items"][0]["run_id"] == "bad-1"


def test_list_runs_pagination(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        for i in range(5):
            with run_scope(session, project_id=pid, run_id=f"page-{i}"):
                pass

    with transactional(factory) as session:
        page = list_runs_for_project(session, pid, limit=2, offset=2)
    assert len(page["items"]) == 2
    assert page["limit"] == 2 and page["offset"] == 2 and page["total"] == 5


def test_list_runs_isolated_per_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Run-list never leaks between projects."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        a = _add_project(session, slug="proj-a")
        b = _add_project(session, slug="proj-b")
        with run_scope(session, project_id=a, run_id="a-1"):
            pass
        with run_scope(session, project_id=b, run_id="b-1"):
            pass

    with transactional(factory) as session:
        ra = list_runs_for_project(session, a)
        rb = list_runs_for_project(session, b)

    assert {it["run_id"] for it in ra["items"]} == {"a-1"}
    assert {it["run_id"] for it in rb["items"]} == {"b-1"}


# --------------------------------------------------------------------------- #
# get_run_with_mutations                                                       #
# --------------------------------------------------------------------------- #


def test_get_run_unknown_returns_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _add_project(session)
        result = get_run_with_mutations(session, "does-not-exist")
    assert result is None


def test_get_run_with_revisions_and_audit_log(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        with run_scope(session, project_id=pid, run_id="run-mutations"):
            for i in range(3):
                rev.write(
                    session,
                    project_id=pid,
                    entity_kind=EntityKind.TASK,
                    entity_id=i + 1,
                    author="agent",
                    diff='{"op":"create"}',
                )
            session.add(
                AuditLogModel(
                    project_id=pid,
                    actor="agent",
                    surface="mcp",
                    action="task.complete",
                    payload_json={"task_id": "PCA-032"},
                    result="ok",
                    run_id="run-mutations",
                )
            )

    with transactional(factory) as session:
        result = get_run_with_mutations(session, "run-mutations")

    assert result is not None
    assert result["run_id"] == "run-mutations"
    assert result["status"] == "done"
    assert len(result["mutations"]["revisions"]) == 3
    assert len(result["mutations"]["audit_log"]) == 1
    assert result["mutations"]["audit_log"][0]["action"] == "task.complete"


def test_get_run_excludes_other_runs_mutations(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Mutations from a different run must NOT appear in run.get."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        with run_scope(session, project_id=pid, run_id="run-A"):
            rev.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.TASK,
                entity_id=1,
                author="agent",
                diff='{"op":"create"}',
            )
        with run_scope(session, project_id=pid, run_id="run-B"):
            rev.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.TASK,
                entity_id=2,
                author="agent",
                diff='{"op":"create"}',
            )

    with transactional(factory) as session:
        a = get_run_with_mutations(session, "run-A")
        b = get_run_with_mutations(session, "run-B")

    assert a is not None and b is not None
    assert {r["entity_id"] for r in a["mutations"]["revisions"]} == {1}
    assert {r["entity_id"] for r in b["mutations"]["revisions"]} == {2}


# --------------------------------------------------------------------------- #
# plan_run_revert (PCA-033 dry_run)                                            #
# --------------------------------------------------------------------------- #


def test_plan_run_revert_unknown_run_id_returns_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.mcp.tools.run_tools import plan_run_revert

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _add_project(session)
        result = plan_run_revert(session, "does-not-exist")
    assert result is None


def test_plan_run_revert_no_conflicts_lists_all_ops(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Run with 3 revisions, no later writes → 3 ops, 0 conflicts."""
    from cod_doc.mcp.tools.run_tools import plan_run_revert

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        with run_scope(session, project_id=pid, run_id="rv-clean"):
            for i in range(3):
                rev.write(
                    session,
                    project_id=pid,
                    entity_kind=EntityKind.TASK,
                    entity_id=i + 1,
                    author="agent",
                    diff='{"op":"create"}',
                )

    with transactional(factory) as session:
        result = plan_run_revert(session, "rv-clean")

    assert result is not None
    assert result["run_id"] == "rv-clean"
    assert result["status"] == "done"
    assert result["dry_run"] is True
    assert result["total_operations"] == 3
    assert result["total_conflicts"] == 0
    for op in result["operations"]:
        assert op["conflicts"] == []


def test_plan_run_revert_detects_later_human_edits_as_conflicts(
    engine_with_schema,
) -> None:  # type: ignore[no-untyped-def]
    """A later revision on the same entity by a different actor → conflict."""
    from cod_doc.mcp.tools.run_tools import plan_run_revert
    from cod_doc.services.run_context import set_current_run_id

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        with run_scope(session, project_id=pid, run_id="rv-conflicted"):
            rev.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.TASK,
                entity_id=42,
                author="agent",
                diff='{"op":"create"}',
            )
        # Human edit on the same entity, different run_id (None).
        try:
            set_current_run_id(None)
            rev.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.TASK,
                entity_id=42,
                author="human:cli",
                diff='{"op":"status","old":"pending","new":"done"}',
            )
        finally:
            set_current_run_id(None)

    with transactional(factory) as session:
        result = plan_run_revert(session, "rv-conflicted")

    assert result is not None
    assert result["total_operations"] == 1
    assert result["total_conflicts"] == 1
    op = result["operations"][0]
    assert len(op["conflicts"]) == 1
    assert op["conflicts"][0]["author"] == "human:cli"
    assert op["conflicts"][0]["run_id"] is None


def test_plan_run_revert_does_not_count_same_run_revs_as_conflicts(
    engine_with_schema,
) -> None:  # type: ignore[no-untyped-def]
    """Multiple revisions of the same entity *within* the run are not conflicts."""
    from cod_doc.mcp.tools.run_tools import plan_run_revert

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _add_project(session)
        with run_scope(session, project_id=pid, run_id="rv-self"):
            rev.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.TASK,
                entity_id=7,
                author="agent",
                diff='{"op":"create"}',
            )
            rev.write(
                session,
                project_id=pid,
                entity_kind=EntityKind.TASK,
                entity_id=7,
                author="agent",
                diff='{"op":"status","old":"pending","new":"in-progress"}',
            )

    with transactional(factory) as session:
        result = plan_run_revert(session, "rv-self")

    assert result is not None
    assert result["total_operations"] == 2
    assert result["total_conflicts"] == 0  # both revs are part of the same run
    for op in result["operations"]:
        assert op["conflicts"] == []
