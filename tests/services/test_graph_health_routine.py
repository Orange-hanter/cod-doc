"""Рутина graph_health в каталоге routine_service (ADO-205, RFC 26 §5.3).

Эталоны — литералы и прямые SELECT по ``finding``. Ожидание не строится ни
через ``graph_health.assess/sync``, ни через ``curator_service``: иначе тест
повторял бы дефект, который должен ловить.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DependencyModel, FindingModel, ProjectModel
from cod_doc.services import curator_service, plan_service, task_service
from cod_doc.services import routine_service as routines

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

AUTHOR = "human:test"


def _project(session: Session, slug: str, root: Path) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=str(root), config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _task(session: Session, pid: int, plan_id: int, section_id: int, task_id: str) -> int:
    task = task_service.create(
        session,
        project_id=pid,
        plan_id=plan_id,
        section_id=section_id,
        task_id=task_id,
        title=f"Задача {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=AUTHOR,
    )
    assert task.row_id is not None
    return task.row_id


def _seed_mute_edge(session: Session, root: Path) -> int:
    """План с одной корректной секцией и немым ребром TA-002 → TA-001."""
    pid = _project(session, "gh", root)
    plan = plan_service.create_plan(
        session, project_id=pid, scope="gh-plan", principle=None, author=AUTHOR
    )
    section = plan_service.create_section(
        session, project_id=pid, plan_scope="gh-plan", letter="A", title="Data Core", author=AUTHOR
    )
    assert plan.row_id is not None
    assert section.row_id is not None
    t1 = _task(session, pid, plan.row_id, section.row_id, "TA-001")
    t2 = _task(session, pid, plan.row_id, section.row_id, "TA-002")
    session.add(DependencyModel(from_task_id=t2, to_task_id=t1, kind="blocks", note=None))
    session.flush()
    routines.create(session, pid, name="graph", check_name="graph_health", trigger="manual")
    return pid


def _graph_rows(session: Session, pid: int) -> list[FindingModel]:
    return list(
        session.execute(
            select(FindingModel).where(
                FindingModel.project_id == pid, FindingModel.source_ref == "graph_health"
            )
        ).scalars()
    )


def test_graph_health_in_catalog() -> None:
    assert "graph_health" in routines.CHECK_CATALOG
    assert routines.__doc__ is not None
    assert "graph_health" in routines.__doc__


def test_routine_run_writes_findings(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_mute_edge(session, tmp_path)
        routines.run_now(session, pid, "graph")

    with transactional(factory) as session:
        rows = _graph_rows(session, pid)
        assert len(rows) == 1
        assert rows[0].status == "open"
        assert rows[0].times_seen == 1

    with transactional(factory) as session:
        routines.run_now(session, pid, "graph")

    with transactional(factory) as session:
        rows = _graph_rows(session, pid)
        assert len(rows) == 1
        assert rows[0].status == "open"
        assert rows[0].times_seen == 2


def test_findings_visible_in_curator_next(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_mute_edge(session, tmp_path)
        routines.run_now(session, pid, "graph")

    with transactional(factory, commit=False) as session:
        payload = curator_service.next(
            session,
            project_id=pid,
            root_path=tmp_path,
            master_path=tmp_path / "MASTER.md",
            project_slug="gh",
            skip_links=True,
        )

    matching = [
        f
        for f in payload["card"]["findings"]
        if "TA-002" in f"{f.get('title')} {f.get('ref')}"
        and "TA-001" in f"{f.get('title')} {f.get('ref')}"
    ]
    assert len(matching) == 1


def test_empty_project_routine_silent(engine_with_schema, tmp_path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session, "empty", tmp_path)
        routines.create(session, pid, name="graph", check_name="graph_health", trigger="manual")
        run = routines.run_now(session, pid, "graph")
        assert run.status != "error"
        assert run.error is None
        assert run.findings_count == 0

    with transactional(factory) as session:
        count = session.execute(
            select(func.count())
            .select_from(FindingModel)
            .where(FindingModel.project_id == pid, FindingModel.source_ref == "graph_health")
        ).scalar_one()
        assert count == 0
