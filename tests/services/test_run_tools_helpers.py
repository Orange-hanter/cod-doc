"""PCA-032: run_tools query helpers (list_runs_for_project + get_run_with_mutations).

ADR-012 (ADO-044): `plan_run_revert` удалён вместе с тулом `run_revert`,
`audit_log` — вместе с таблицей (миграция 0029). Осталось то, что
продолжает работать для встроенного оркестратора.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
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
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
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


def test_get_run_with_revisions(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
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

    with transactional(factory) as session:
        result = get_run_with_mutations(session, "run-mutations")

    assert result is not None
    assert result["run_id"] == "run-mutations"
    assert result["status"] == "done"
    assert len(result["mutations"]["revisions"]) == 3
    # ADR-012: ключа audit_log больше нет — таблица удалена миграцией 0029.
    assert "audit_log" not in result["mutations"]


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
