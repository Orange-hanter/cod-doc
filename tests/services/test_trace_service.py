"""COD-063: trace_service — record + list_for_task + timed_call helper."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import task_service
from cod_doc.services import trace_service as traces

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="trace-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make_task(session: Session, p: int, pl: int, s: int, tid: str) -> int:
    t = task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    assert t.row_id is not None
    return t.row_id


def test_record_persists_minimal_chat_call(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        tid = _make_task(session, p, pl, s, "TR-001")
        rec = traces.record(
            session,
            model="anthropic/claude-sonnet-4-6",
            task_id=tid,
            input_tokens=120,
            output_tokens=42,
            duration_ms=850,
        )
        assert rec.row_id is not None
        assert rec.task_id == tid
        assert rec.total_tokens == 162


def test_list_for_task_returns_newest_first(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        tid = _make_task(session, p, pl, s, "TR-001")

        traces.record(session, model="m1", task_id=tid, input_tokens=1)
        traces.record(session, model="m2", task_id=tid, input_tokens=2)
        traces.record(session, model="m3", task_id=tid, input_tokens=3)

        rows = traces.list_for_task(session, tid)
        assert [r.model for r in rows] == ["m3", "m2", "m1"]


def test_list_excludes_other_tasks(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _make_task(session, p, pl, s, "TR-001")
        b = _make_task(session, p, pl, s, "TR-002")

        traces.record(session, model="m-A", task_id=a)
        traces.record(session, model="m-B", task_id=b)
        traces.record(session, model="m-orphan", task_id=None)

        a_rows = traces.list_for_task(session, a)
        assert [r.model for r in a_rows] == ["m-A"]


def test_record_serializes_tool_calls(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        tid = _make_task(session, p, pl, s, "TR-001")
        tool_calls = [
            {"name": "task.list", "args": {"project": "demo"}},
            {"name": "doc.get", "args": {"key": "MASTER"}},
        ]
        traces.record(session, model="m", task_id=tid, tool_calls=tool_calls, input_tokens=1)

        [row] = traces.list_for_task(session, tid)
        assert row.tool_calls == tool_calls


def test_timed_call_measures_duration_and_captures_error() -> None:
    c = traces.TraceCollector()
    with traces.timed_call(c):
        time.sleep(0.01)
    assert c.duration_ms >= 10
    assert c.error is None

    c2 = traces.TraceCollector()
    with pytest.raises(RuntimeError), traces.timed_call(c2):
        raise RuntimeError("boom")
    assert c2.error is not None and "RuntimeError" in c2.error and "boom" in c2.error
    assert c2.duration_ms >= 0
