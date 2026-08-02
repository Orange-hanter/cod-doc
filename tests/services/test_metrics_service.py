"""OBI-001: TaskMetricsService records on task.complete; summary + sparkline."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskMetricsModel,
    TaskModel,
)
from cod_doc.services import metrics_service, task_service


def _seed(session) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="metp", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="metp-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _make(session, pid, plid, sid, tid, *, type=TaskType.FEATURE, prio=Priority.MEDIUM):
    return task_service.create(
        session,
        project_id=pid,
        plan_id=plid,
        section_id=sid,
        task_id=tid,
        title=f"task {tid}",
        type=type,
        priority=prio,
        author="t",
    )


# ----------------------------------------------------------------- #
# Auto-record on complete()                                          #
# ----------------------------------------------------------------- #


def test_complete_records_metrics_row(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "MET-001")
    with transactional(factory) as session:
        task_service.complete(session, task_id="MET-001", author="a")
    with transactional(factory) as session:
        rows = list(session.execute(select(TaskMetricsModel)).scalars())
    assert len(rows) == 1
    m = rows[0]
    assert m.priority == "medium"
    assert m.type == "feature"
    assert m.duration_hours >= 0
    assert m.commit_count == 0  # populated later by OBI-010


def test_record_is_idempotent_on_recomplete(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Re-completing the same task (after reopen) must not create a 2nd row."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "MET-002")
    with transactional(factory) as session:
        task_service.complete(session, task_id="MET-002", author="a")
    # Reopen via direct model touch (state-machine path is checked elsewhere).
    with transactional(factory) as session:
        m = session.execute(select(TaskModel).where(TaskModel.task_id == "MET-002")).scalar_one()
        m.status = TaskStatus.IN_PROGRESS.value
    # Complete again — second call should be a no-op for metrics.
    with transactional(factory) as session:
        task_service.complete(session, task_id="MET-002", author="a")
    with transactional(factory) as session:
        rows = list(session.execute(select(TaskMetricsModel)).scalars())
    assert len(rows) == 1


# ----------------------------------------------------------------- #
# summary() — counts + percentiles                                   #
# ----------------------------------------------------------------- #


def test_summary_empty(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with transactional(factory) as session:
        s = metrics_service.summary(session, project_id=1)
    assert s["completed"] == 0
    assert s["duration_hours"]["p50"] is None


def test_summary_returns_percentiles_by_type(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "MET-010", type=TaskType.FEATURE)
        _make(session, pid, plid, sid, "MET-011", type=TaskType.FEATURE)
        _make(session, pid, plid, sid, "MET-012", type=TaskType.BUG)
    with transactional(factory) as session:
        for tid in ("MET-010", "MET-011", "MET-012"):
            task_service.complete(session, task_id=tid, author="a")
    with transactional(factory) as session:
        s = metrics_service.summary(session, project_id=1)
    assert s["completed"] == 3
    assert "feature" in s["by_type"]
    assert "bug" in s["by_type"]
    assert s["by_type"]["feature"]["n"] == 2
    assert s["by_type"]["bug"]["n"] == 1
    # Percentiles are non-negative (rough sanity — durations≥0).
    assert s["duration_hours"]["p50"] is not None
    assert s["duration_hours"]["p50"] >= 0


# ----------------------------------------------------------------- #
# sparkline_buckets()                                                #
# ----------------------------------------------------------------- #


def test_sparkline_returns_fixed_length(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
    with transactional(factory) as session:
        buckets = metrics_service.sparkline_buckets(session, project_id=1, days=14)
    assert len(buckets) == 14
    assert all("date" in b and "count" in b for b in buckets)


def test_sparkline_counts_today_completion(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, plid, sid = _seed(session)
        _make(session, pid, plid, sid, "MET-020")
    with transactional(factory) as session:
        task_service.complete(session, task_id="MET-020", author="a")
    with transactional(factory) as session:
        buckets = metrics_service.sparkline_buckets(session, project_id=1, days=7)
    # Today's bucket has count=1.
    today = datetime.now(UTC).date().isoformat()
    today_b = next(b for b in buckets if b["date"] == today)
    assert today_b["count"] == 1
