"""AFT-006 (RFC 27 F7): filters for task_service.list_for_project/count_for_project.

Покрывает plan_scope / section_letter / type / completed_since /
updated_since / has_commit, резолв-ошибки и перенос plan_scope /
section_letter в строку ``task_to_dict``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import task_service
from cod_doc.services.serializers import task_to_dict

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Канон: какая задача где лежит.
#   plan-x: секция A → PXA-001; секция C → PXC-001..PXC-005
#   plan-y: секция C → PYC-001
PLAN_X_TASKS = {"PXA-001", "PXC-001", "PXC-002", "PXC-003", "PXC-004", "PXC-005"}
PLAN_X_SECTION_C = {"PXC-001", "PXC-002", "PXC-003", "PXC-004", "PXC-005"}
BUG_TASKS = {"PXC-001", "PXC-002", "PYC-001"}

_DONE_AT_WITH_COMMIT = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)  # PXC-002, sha abc1234
_DONE_AT_NO_COMMIT_A = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)  # PXC-003, commit None
_DONE_AT_NO_COMMIT_B = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)  # PXC-004, commit ''
_DONE_AT_OLD = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)  # PXC-005, sha def5678
_SINCE = datetime(2026, 9, 16, 0, 0, tzinfo=UTC)

_LAST_UPDATED = {
    "PXA-001": datetime(2026, 9, 10, 10, 0, tzinfo=UTC),
    "PXC-001": datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
    "PXC-002": datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
    "PXC-003": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    "PXC-004": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    "PXC-005": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    "PYC-001": datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
}
_UPDATED_SINCE = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
_UPDATED_MATCHING = {"PXC-001", "PXC-002"}


def _add_section(session: Session, plan_id: int, letter: str, position: int) -> PlanSectionModel:
    sec = PlanSectionModel(
        plan_id=plan_id,
        letter=letter,
        title=f"Section {letter}",
        slug=f"{letter}-Section-{letter}",
        position=position,
    )
    session.add(sec)
    session.flush()
    return sec


def _make(
    session: Session,
    proj: int,
    plan: PlanModel,
    sec: PlanSectionModel,
    tid: str,
    *,
    type: TaskType,
    status: TaskStatus = TaskStatus.TODO,
    completed_at: datetime | None = None,
    completed_commit: str | None = None,
) -> None:
    task = task_service.create(
        session,
        project_id=proj,
        plan_id=plan.row_id,
        section_id=sec.row_id,
        task_id=tid,
        title=f"Implement: {tid}",
        type=type,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    assert task.row_id is not None
    model = session.get(TaskModel, task.row_id)
    assert model is not None
    model.status = status.value
    model.completed_at = completed_at
    model.completed_commit = completed_commit
    model.last_updated = _LAST_UPDATED[tid]
    session.flush()


def _seed(session: Session) -> int:
    """Проект; план 'plan-x' с секциями 'A' и 'C'; план 'plan-y' с секцией 'C'."""
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan_x = PlanModel(project_id=proj.row_id, scope="plan-x", created=now, last_updated=now)
    plan_y = PlanModel(project_id=proj.row_id, scope="plan-y", created=now, last_updated=now)
    session.add_all([plan_x, plan_y])
    session.flush()
    x_a = _add_section(session, plan_x.row_id, "A", 0)
    x_c = _add_section(session, plan_x.row_id, "C", 1)
    y_c = _add_section(session, plan_y.row_id, "C", 0)

    _make(session, proj.row_id, plan_x, x_a, "PXA-001", type=TaskType.FEATURE)
    _make(session, proj.row_id, plan_x, x_c, "PXC-001", type=TaskType.BUG)
    _make(
        session,
        proj.row_id,
        plan_x,
        x_c,
        "PXC-002",
        type=TaskType.BUG,
        status=TaskStatus.DONE,
        completed_at=_DONE_AT_WITH_COMMIT,
        completed_commit="abc1234",
    )
    _make(
        session,
        proj.row_id,
        plan_x,
        x_c,
        "PXC-003",
        type=TaskType.CHORE,
        status=TaskStatus.DONE,
        completed_at=_DONE_AT_NO_COMMIT_A,
        completed_commit=None,
    )
    _make(
        session,
        proj.row_id,
        plan_x,
        x_c,
        "PXC-004",
        type=TaskType.CHORE,
        status=TaskStatus.DONE,
        completed_at=_DONE_AT_NO_COMMIT_B,
        completed_commit="",
    )
    _make(
        session,
        proj.row_id,
        plan_x,
        x_c,
        "PXC-005",
        type=TaskType.DOCS,
        status=TaskStatus.DONE,
        completed_at=_DONE_AT_OLD,
        completed_commit="def5678",
    )
    _make(session, proj.row_id, plan_y, y_c, "PYC-001", type=TaskType.BUG)
    return proj.row_id


def _ids(rows: list) -> set[str]:  # type: ignore[type-arg]
    return {r.task_id for r in rows}


def test_filter_plan_scope(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        rows = task_service.list_for_project(session, proj, plan_scope="plan-x")
        assert _ids(rows) == PLAN_X_TASKS
        assert "PYC-001" not in _ids(rows)


def test_filter_section_letter_within_plan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        rows = task_service.list_for_project(session, proj, plan_scope="plan-x", section_letter="C")
        assert _ids(rows) == PLAN_X_SECTION_C
        assert "PYC-001" not in _ids(rows)


def test_section_letter_without_plan_scope_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        with pytest.raises(ValueError, match="plan_scope"):
            task_service.list_for_project(session, proj, section_letter="C")


def test_unknown_plan_or_section_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        with pytest.raises(ValueError, match="Plan 'nope' not found in project"):
            task_service.list_for_project(session, proj, plan_scope="nope")
        with pytest.raises(ValueError, match="Section 'Z' not found in plan 'plan-x'"):
            task_service.list_for_project(session, proj, plan_scope="plan-x", section_letter="Z")


def test_filter_type(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        rows = task_service.list_for_project(session, proj, type=TaskType.BUG)
        assert _ids(rows) == BUG_TASKS


def test_filter_completed_since_and_has_commit(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        rows = task_service.list_for_project(
            session,
            proj,
            status=TaskStatus.DONE,
            completed_since=_SINCE,
            has_commit=True,
        )
        assert _ids(rows) == {"PXC-002"}

        rows = task_service.list_for_project(
            session,
            proj,
            status=TaskStatus.DONE,
            completed_since=_SINCE,
            has_commit=False,
        )
        assert _ids(rows) == {"PXC-003", "PXC-004"}


def test_filter_updated_since(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        rows = task_service.list_for_project(session, proj, updated_since=_UPDATED_SINCE)
        assert _ids(rows) == _UPDATED_MATCHING


def test_count_matches_list_under_filters(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj = _seed(session)

        assert task_service.count_for_project(session, proj, plan_scope="plan-x") == len(
            PLAN_X_TASKS
        )
        assert task_service.count_for_project(
            session, proj, plan_scope="plan-x", section_letter="C"
        ) == len(PLAN_X_SECTION_C)
        assert task_service.count_for_project(session, proj, type=TaskType.BUG) == len(BUG_TASKS)
        assert (
            task_service.count_for_project(
                session,
                proj,
                status=TaskStatus.DONE,
                completed_since=_SINCE,
                has_commit=True,
            )
            == 1
        )
        assert (
            task_service.count_for_project(
                session,
                proj,
                status=TaskStatus.DONE,
                completed_since=_SINCE,
                has_commit=False,
            )
            == 2
        )
        assert task_service.count_for_project(session, proj, updated_since=_UPDATED_SINCE) == len(
            _UPDATED_MATCHING
        )


def test_parse_since() -> None:
    assert task_service.parse_since("2026-09-16") == datetime(2026, 9, 16, tzinfo=UTC)
    assert task_service.parse_since("2026-09-16T12:00:00+03:00") == datetime(
        2026, 9, 16, 9, 0, tzinfo=UTC
    )
    assert task_service.parse_since("2026-09-16T12:00:00") == datetime(
        2026, 9, 16, 12, 0, tzinfo=UTC
    )
    with pytest.raises(ValueError, match="not-a-date"):
        task_service.parse_since("not-a-date")


def test_task_to_dict_carries_plan_scope_and_section_letter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)

        task = task_service.get(session, "PXC-001")
        assert task is not None
        row = task_to_dict(task, session=session)
        assert row["plan_scope"] == "plan-x"
        assert row["section_letter"] == "C"

        row = task_to_dict(task)
        assert "plan_scope" in row and row["plan_scope"] is None
        assert "section_letter" in row and row["section_letter"] is None
