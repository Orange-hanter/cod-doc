"""ADO-025: remove_dependency — delete a task→task 'blocks' edge."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import plan_service, task_service
from cod_doc.services import revision_service as rev
from cod_doc.services.task_service import DependencyNotFoundError, TaskNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(
    session: Session,
    p: int,
    pl: int,
    s: int,
    tid: str,
    *,
    blocked_by: list[str] | None = None,
) -> None:
    task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        blocked_by=blocked_by,
    )


def _edges(session: Session) -> list[tuple[int, int, str]]:
    return [
        (d.from_task_id, d.to_task_id, d.kind)
        for d in session.execute(select(DependencyModel)).scalars()
    ]


def test_remove_dependency_deletes_edge_and_unblocks_ready(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "MY-001")
        _make(session, p, pl, s, "MY-002", blocked_by=["MY-001"])

        ready_ids = {t.task_id for t in plan_service.ready(session, pl)}
        assert "MY-002" not in ready_ids

        task_service.remove_dependency(
            session, task_id="MY-002", blocker_task_id="MY-001", author="human:test"
        )

        assert _edges(session) == []
        ready_ids = {t.task_id for t in plan_service.ready(session, pl)}
        assert "MY-002" in ready_ids


def test_remove_dependency_only_removes_the_requested_edge(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "MY-001")
        _make(session, p, pl, s, "MY-002")
        _make(session, p, pl, s, "MY-003", blocked_by=["MY-001", "MY-002"])

        task_service.remove_dependency(
            session, task_id="MY-003", blocker_task_id="MY-001", author="human:test"
        )

        edges = _edges(session)
        assert len(edges) == 1
        blocker = task_service.get(session, "MY-002")
        task = task_service.get(session, "MY-003")
        assert blocker is not None and task is not None
        assert edges == [(task.row_id, blocker.row_id, "blocks")]


def test_remove_dependency_missing_edge_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "MY-001")
        _make(session, p, pl, s, "MY-002")

        with pytest.raises(DependencyNotFoundError, match="MY-002"):
            task_service.remove_dependency(
                session, task_id="MY-002", blocker_task_id="MY-001", author="human:test"
            )


def test_remove_dependency_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "MY-001", blocked_by=[])
        _make(session, p, pl, s, "MY-002", blocked_by=["MY-001"])

        with pytest.raises(TaskNotFoundError):
            task_service.remove_dependency(
                session, task_id="NOPE-1", blocker_task_id="MY-001", author="human:test"
            )
        with pytest.raises(TaskNotFoundError):
            task_service.remove_dependency(
                session, task_id="MY-002", blocker_task_id="NOPE-2", author="human:test"
            )


def test_remove_dependency_writes_revision_and_activity(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _make(session, p, pl, s, "MY-001")
        _make(session, p, pl, s, "MY-002", blocked_by=["MY-001"])

        task = task_service.get(session, "MY-002")
        assert task is not None and task.row_id is not None

        task_service.remove_dependency(
            session,
            task_id="MY-002",
            blocker_task_id="MY-001",
            author="human:test",
            reason="spec changed",
        )

        history = rev.list_for_entity(session, EntityKind.TASK, task.row_id)
        last = history[-1]
        diff = json.loads(last.diff)
        assert diff["op"] == "remove_dependency"
        assert diff["blocker"] == "MY-001"
        assert last.reason == "spec changed"

        # activity_service.emit adds the row without flushing, and these
        # sessions run with autoflush disabled — flush before querying.
        session.flush()
        events = list(
            session.execute(
                select(ActivityEventModel).where(
                    ActivityEventModel.kind == "task.dependency_removed",
                    ActivityEventModel.scope_id == "MY-002",
                )
            ).scalars()
        )
        assert len(events) == 1
        assert events[0].payload["blocker_task_id"] == "MY-001"
