"""ADO-202 (RFC 26 §3.2, задача 3): task_service.add_dependency.

Upsert ребра с обязательным ``note``, отказ при петле, чужом блокере и
замыкании цикла; ``dependency_warnings`` — отдельная read-функция.
Эталоны — литералы и прямые SELECT по ``dependency``, ``revision``,
``activity_event`` и вьюхе ``ready_tasks``; ожидание через ``_reaches`` /
``_upsert_edge`` / повторный ``add_dependency`` не строится.
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select, text

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import event_bus, task_service
from cod_doc.services.task_service import DependencyCycleError, TaskNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

AUTHOR = "human:test"


def _seed_project(session: Session, slug: str) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope=f"{slug}-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _seed_plan(session: Session, project_id: int, scope: str) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    plan = PlanModel(project_id=project_id, scope=scope, created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0)
    session.add(sec)
    session.flush()
    return project_id, plan.row_id, sec.row_id


def _make(
    session: Session,
    ids: tuple[int, int, int],
    tid: str,
    *,
    blocked_by: list[str] | None = None,
) -> None:
    p, pl, s = ids
    task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=AUTHOR,
        blocked_by=blocked_by,
    )


def _row_id(session: Session, project_id: int, task_id: str) -> int:
    return session.execute(
        select(TaskModel.row_id).where(
            TaskModel.project_id == project_id, TaskModel.task_id == task_id
        )
    ).scalar_one()


def _edges(session: Session) -> list[tuple[int, int, str, str | None]]:
    return [
        (r[0], r[1], r[2], r[3])
        for r in session.execute(
            select(
                DependencyModel.from_task_id,
                DependencyModel.to_task_id,
                DependencyModel.kind,
                DependencyModel.note,
            )
        ).all()
    ]


def _count(session: Session, sql: str, **params: Any) -> int:
    session.flush()  # text() не делает autoflush: события лежат в сессии
    return int(session.execute(text(sql), params).scalar_one())


def _revisions(session: Session) -> int:
    return _count(session, "SELECT COUNT(*) FROM revision WHERE entity_kind = 'task'")


def _events(session: Session, kind: str | None = None) -> int:
    if kind is None:
        return _count(session, "SELECT COUNT(*) FROM activity_event")
    return _count(session, "SELECT COUNT(*) FROM activity_event WHERE kind = :k", k=kind)


def _last_task_diff(session: Session) -> dict[str, Any]:
    raw = session.execute(
        text("SELECT diff FROM revision WHERE entity_kind = 'task' ORDER BY row_id DESC LIMIT 1")
    ).scalar_one()
    return dict(json.loads(raw))


def _add(session: Session, project_id: int, task: str, blocker: str, note: str, **kw: Any) -> Any:
    return task_service.add_dependency(
        session,
        project_id=project_id,
        task_id=task,
        blocker_task_id=blocker,
        note=note,
        author=AUTHOR,
        **kw,
    )


def test_add_creates_edge_with_note(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")
        revs, events = _revisions(session), _events(session, "task.dependency_added")

        change = _add(session, alpha[0], "TA-002", "TA-001", "нужен планировщик")

        assert change.op == "add_dependency"
        assert change.task.task_id == "TA-002"
        assert _edges(session) == [
            (
                _row_id(session, alpha[0], "TA-002"),
                _row_id(session, alpha[0], "TA-001"),
                "blocks",
                "нужен планировщик",
            )
        ]
        assert _revisions(session) == revs + 1
        assert _last_task_diff(session)["op"] == "add_dependency"
        assert _events(session, "task.dependency_added") == events + 1


def test_note_is_required(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")
        revs, events = _revisions(session), _events(session)

        for note in ("", "   "):
            for adopt in (False, True):
                with pytest.raises(ValueError, match="note"):
                    _add(session, alpha[0], "TA-002", "TA-001", note, adopt=adopt)

        assert len(_edges(session)) == 0
        assert _revisions(session) - revs == 0
        assert _events(session) - events == 0


def test_repeat_same_note_is_silent(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")
        _add(session, alpha[0], "TA-002", "TA-001", "нужен планировщик")
        revs, events = _revisions(session), _events(session)

        change = _add(session, alpha[0], "TA-002", "TA-001", "нужен планировщик")

        assert change.op is None
        assert _revisions(session) - revs == 0
        assert _events(session) - events == 0
        assert len(_edges(session)) == 1


def test_other_note_updates(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")
        _add(session, alpha[0], "TA-002", "TA-001", "старая мотивация")
        revs = _revisions(session)

        change = _add(session, alpha[0], "TA-002", "TA-001", "новая мотивация")

        assert change.op == "update_dependency_note"
        edges = _edges(session)
        assert len(edges) == 1
        assert edges[0][3] == "новая мотивация"
        assert _revisions(session) == revs + 1
        diff = _last_task_diff(session)
        assert diff["op"] == "update_dependency_note"
        assert diff["previous_note"] == "старая мотивация"
        assert _events(session, "task.dependency_updated") == 1


def test_adopt_writes_revision_without_data_change(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")
        task_row = _row_id(session, alpha[0], "TA-002")
        blocker_row = _row_id(session, alpha[0], "TA-001")
        # Внесистемная правка: ребро положено в базу мимо приложения.
        session.add(
            DependencyModel(
                from_task_id=task_row, to_task_id=blocker_row, kind="blocks", note="руками"
            )
        )
        session.flush()
        revs, events = _revisions(session), _events(session, "task.dependency_updated")

        change = _add(session, alpha[0], "TA-002", "TA-001", "руками", adopt=True)

        assert change.op == "adopt_dependency"
        assert _edges(session) == [(task_row, blocker_row, "blocks", "руками")]
        assert _revisions(session) == revs + 1
        assert _last_task_diff(session)["op"] == "adopt_dependency"
        assert _events(session, "task.dependency_updated") == events + 1


def test_cycle_refused_with_path(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002", blocked_by=["TA-001"])
        _make(session, alpha, "TA-003", blocked_by=["TA-002"])
        revs = _revisions(session)

        with pytest.raises(DependencyCycleError) as exc_info:
            _add(session, alpha[0], "TA-001", "TA-003", "замкнуть")

        assert exc_info.value.path == ["TA-003", "TA-002", "TA-001"]
        for tid in ("TA-001", "TA-002", "TA-003"):
            assert tid in str(exc_info.value)
        assert len(_edges(session)) == 2
        assert _revisions(session) - revs == 0


def test_self_loop_refused_before_db(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")

        with pytest.raises(DependencyCycleError) as exc_info:
            _add(session, alpha[0], "TA-001", "TA-001", "петля")

        assert exc_info.value.path == ["TA-001"]
        assert len(_edges(session)) == 0


def test_foreign_project_blocker_refused(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        beta = _seed_project(session, "beta")
        _make(session, alpha, "TA-001")
        _make(session, beta, "TA-002")

        with pytest.raises(TaskNotFoundError):
            _add(session, beta[0], "TA-002", "TA-001", "чужой блокер")

        assert len(_edges(session)) == 0


def test_ready_tasks_membership(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")

        def ready() -> set[str]:
            return set(session.execute(text("SELECT task_id FROM ready_tasks")).scalars())

        assert "TA-002" in ready()
        _add(session, alpha[0], "TA-002", "TA-001", "нужен планировщик")
        assert "TA-002" not in ready()
        assert "TA-001" in ready()


def _calls_in(fn_name: str, tree: ast.Module) -> set[str]:
    fn = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == fn_name
    )
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def test_event_bus_symmetry(engine_with_schema: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    def record(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(event_bus, "queue_emit", record)
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")
        calls.clear()

        _add(session, alpha[0], "TA-002", "TA-001", "нужен планировщик")
        task_service.remove_dependency(
            session,
            project_id=alpha[0],
            task_id="TA-002",
            blocker_task_id="TA-001",
            author=AUTHOR,
        )

    assert calls == []
    tree = ast.parse(inspect.getsource(task_service))
    for fn_name in ("add_dependency", "remove_dependency"):
        assert "queue_emit" not in _calls_in(fn_name, tree)


def _codes(session: Session, project_id: int, task: str, blocker: str) -> list[str]:
    return [
        w["code"]
        for w in task_service.dependency_warnings(
            session, project_id=project_id, task_id=task, blocker_task_id=blocker
        )
    ]


def test_dependency_warnings_codes(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002")
        assert _codes(session, alpha[0], "TA-002", "TA-001") == []

        blocker = session.get(TaskModel, _row_id(session, alpha[0], "TA-001"))
        assert blocker is not None
        blocker.status = "done"
        session.flush()
        assert _codes(session, alpha[0], "TA-002", "TA-001") == ["blocker_closed"]
        blocker.status = "cancelled"
        session.flush()
        assert _codes(session, alpha[0], "TA-002", "TA-001") == ["blocker_closed"]
        blocker.status = "todo"

        task = session.get(TaskModel, _row_id(session, alpha[0], "TA-002"))
        assert task is not None
        task.status = "in_progress"
        session.flush()
        assert "task_in_progress" in _codes(session, alpha[0], "TA-002", "TA-001")
        task.status = "todo"
        task.checked_out_by = "agent:x"
        session.flush()
        assert "task_in_progress" in _codes(session, alpha[0], "TA-002", "TA-001")
        task.checked_out_by = None
        session.flush()

        other_plan = _seed_plan(session, alpha[0], "alpha-other")
        _make(session, other_plan, "TA-010")
        assert "cross_plan" in _codes(session, alpha[0], "TA-010", "TA-001")

        _make(session, alpha, "TA-003")
        _add(session, alpha[0], "TA-002", "TA-001", "шаг 1")
        _add(session, alpha[0], "TA-003", "TA-002", "шаг 2")
        assert _codes(session, alpha[0], "TA-003", "TA-001") == ["transitive"]
        _add(session, alpha[0], "TA-003", "TA-001", "напрямую")
        assert _codes(session, alpha[0], "TA-003", "TA-001") == ["transitive"]
        # Само прямое ребро без обходного пути транзитивным не считается.
        assert _codes(session, alpha[0], "TA-002", "TA-001") == []


def test_dependency_warnings_writes_nothing(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        alpha = _seed_project(session, "alpha")
        _make(session, alpha, "TA-001")
        _make(session, alpha, "TA-002", blocked_by=["TA-001"])
        _make(session, alpha, "TA-003", blocked_by=["TA-002"])
        revs = _count(session, "SELECT COUNT(*) FROM revision")
        events = _events(session)

        task_service.dependency_warnings(
            session, project_id=alpha[0], task_id="TA-003", blocker_task_id="TA-001"
        )

        assert _count(session, "SELECT COUNT(*) FROM revision") - revs == 0
        assert _events(session) - events == 0
        assert session.execute(select(func.count()).select_from(DependencyModel)).scalar_one() == 2
