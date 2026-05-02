"""COD-021: graph queries — forward/reverse chain + critical path."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, TaskStatus, TaskType
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
)
from cod_doc.services import plan_service as plans
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'graph.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _seed(session: Session) -> tuple[int, int, int]:
    """Return (project_id, plan_id, section_id)."""
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


def _task(session: Session, proj: int, plan: int, sec: int, tid: str) -> int:
    t = tasks.create(
        session,
        project_id=proj,
        plan_id=plan,
        section_id=sec,
        task_id=tid,
        title=f"Implement: {tid}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    assert t.row_id is not None
    return t.row_id


def _dep(session: Session, blocked_id: int, blocker_id: int) -> None:
    """blocked_id depends on blocker_id (blocker must finish first)."""
    session.add(DependencyModel(from_task_id=blocked_id, to_task_id=blocker_id, kind="blocks"))
    session.flush()


# -----------------------------------------------------------------------
# forward_chain  (prerequisites: what must be done BEFORE task_id)
# -----------------------------------------------------------------------


def test_forward_chain_empty_for_source_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _task(session, p, pl, s, "GR-001")
        chain = plans.forward_chain(session, "GR-001")
        assert chain == []


def test_forward_chain_returns_direct_prerequisite(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """GR-002 blocked by GR-001 → forward_chain("GR-002") = [GR-001]."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        _dep(session, b, a)  # GR-002 blocked by GR-001

        chain = plans.forward_chain(session, "GR-002")
        assert {e.task_id for e in chain} == {"GR-001"}


def test_forward_chain_transitive(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A → B → C: forward_chain(C) should include both A and B."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        c = _task(session, p, pl, s, "GR-003")
        _dep(session, b, a)  # B depends on A
        _dep(session, c, b)  # C depends on B

        chain = plans.forward_chain(session, "GR-003")
        ids = {e.task_id for e in chain}
        assert ids == {"GR-001", "GR-002"}


def test_forward_chain_depth_ordering(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Entries are ordered by depth (closest prerequisite first)."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        c = _task(session, p, pl, s, "GR-003")
        _dep(session, b, a)
        _dep(session, c, b)

        chain = plans.forward_chain(session, "GR-003")
        depths = [e.depth for e in chain]
        assert depths == sorted(depths)  # depth-ascending


def test_forward_chain_unknown_task_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(plans.TaskNotFoundInPlanError):
        plans.forward_chain(session, "GHOST-001")


def test_forward_chain_excludes_relates_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Only 'blocks' edges form the prerequisite chain."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        session.add(DependencyModel(from_task_id=b, to_task_id=a, kind="relates"))
        session.flush()

        chain = plans.forward_chain(session, "GR-002")
        assert chain == []


# -----------------------------------------------------------------------
# reverse_chain  (dependents: what unlocks AFTER task_id completes)
# -----------------------------------------------------------------------


def test_reverse_chain_empty_for_leaf_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _task(session, p, pl, s, "GR-001")
        chain = plans.reverse_chain(session, "GR-001")
        assert chain == []


def test_reverse_chain_returns_direct_dependent(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """GR-002 blocked by GR-001 → reverse_chain("GR-001") = [GR-002]."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        _dep(session, b, a)

        chain = plans.reverse_chain(session, "GR-001")
        assert {e.task_id for e in chain} == {"GR-002"}


def test_reverse_chain_transitive(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A → B → C: reverse_chain(A) = {B, C}."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        c = _task(session, p, pl, s, "GR-003")
        _dep(session, b, a)
        _dep(session, c, b)

        chain = plans.reverse_chain(session, "GR-001")
        assert {e.task_id for e in chain} == {"GR-002", "GR-003"}


def test_reverse_chain_multiple_dependents(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A → B, A → C: reverse_chain(A) = {B, C}."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        c = _task(session, p, pl, s, "GR-003")
        _dep(session, b, a)
        _dep(session, c, a)

        chain = plans.reverse_chain(session, "GR-001")
        assert {e.task_id for e in chain} == {"GR-002", "GR-003"}


# -----------------------------------------------------------------------
# critical_path  (longest sequential chain in the plan)
# -----------------------------------------------------------------------


def test_critical_path_empty_plan(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _, plan_id, _ = _seed(session)
        result = plans.critical_path(session, plan_id)
        assert result.length == 0
        assert result.task_ids == []


def test_critical_path_single_task(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _task(session, p, pl, s, "GR-001")
        result = plans.critical_path(session, pl)
        assert result.length == 1
        assert result.task_ids == ["GR-001"]


def test_critical_path_linear_chain(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A → B → C: critical path length = 3, ordered A, B, C."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        c = _task(session, p, pl, s, "GR-003")
        _dep(session, b, a)
        _dep(session, c, b)

        result = plans.critical_path(session, pl)
        assert result.length == 3
        assert result.task_ids == ["GR-001", "GR-002", "GR-003"]


def test_critical_path_picks_longest_branch(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Diamond: A → B → D and A → C → D, plus extra E → D.
    Longest chain: E → D (depth-2), or A → B → D / A → C → D (depth-3).
    Critical path = 3 tasks."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        c = _task(session, p, pl, s, "GR-003")
        d = _task(session, p, pl, s, "GR-004")
        _dep(session, b, a)
        _dep(session, c, a)
        _dep(session, d, b)
        _dep(session, d, c)

        result = plans.critical_path(session, pl)
        assert result.length == 3
        assert result.task_ids[0] == "GR-001"  # source
        assert result.task_ids[-1] == "GR-004"  # end


def test_critical_path_parallel_tasks_dont_inflate(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Tasks with no deps run in parallel; critical path stays 1."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        for n in range(1, 6):
            _task(session, p, pl, s, f"GR-00{n}")

        result = plans.critical_path(session, pl)
        assert result.length == 1  # all independent


def test_critical_path_includes_status_info(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ChainEntry items carry task status."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        a = _task(session, p, pl, s, "GR-001")
        b = _task(session, p, pl, s, "GR-002")
        _dep(session, b, a)
        tasks.complete(session, task_id="GR-001", author="human:test")

        result = plans.critical_path(session, pl)
        ids_to_status = {e.task_id: e.status for e in result.chain}
        assert ids_to_status["GR-001"] is TaskStatus.DONE
        assert ids_to_status["GR-002"] is TaskStatus.PENDING


def test_critical_path_unknown_plan_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(plans.PlanNotFoundError):
        plans.critical_path(session, 9999)
