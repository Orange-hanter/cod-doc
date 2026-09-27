"""Рутина graph_health (ADO-205, RFC 26 §5.3): циклы, немые и мёртвые рёбра, секции.

Эталоны — литералы. Ожидание не строится ни через ``_find_cycles``, ни через
``validate_section_slug``, ни через выборку из ``dependency``/``plan_section``:
иначе тест повторял бы дефект, который должен ловить.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    DependencyModel,
    FindingModel,
    PlanSectionModel,
    ProjectModel,
    TaskModel,
)
from cod_doc.services import finding_service, plan_service, task_service
from cod_doc.services import graph_health as gh
from cod_doc.services.graph_health import EdgeRow, SectionRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
AUTHOR = "human:test"


# ------------------------------------------------------------------ #
# Чистые правила                                                      #
# ------------------------------------------------------------------ #


def _edge(
    frm: tuple[int, str],
    to: tuple[int, str],
    *,
    kind: str = "blocks",
    note: str | None = "потому что",
    from_status: str = "todo",
    to_status: str = "todo",
    from_plan: int = 1,
    to_plan: int = 1,
    to_completed_at: datetime | None = None,
) -> EdgeRow:
    return EdgeRow(
        from_row_id=frm[0],
        to_row_id=to[0],
        from_task_id=frm[1],
        to_task_id=to[1],
        kind=kind,
        note=note,
        from_status=from_status,
        to_status=to_status,
        from_plan_id=from_plan,
        to_plan_id=to_plan,
        to_completed_at=to_completed_at,
    )


def _codes(issues: list[gh.GraphIssue]) -> set[tuple[str, str]]:
    return {(i.code, i.scope_id) for i in issues}


T1, T2, T3 = (7, "TA-001"), (3, "TA-002"), (5, "TA-003")


def test_cycle_detected_via_find_cycles() -> None:
    edges = [_edge(T1, T2), _edge(T2, T3), _edge(T3, T1)]
    assert _codes(gh.assess_cycles(edges)) == {("cycle", "TA-001→TA-002→TA-003")}
    assert len(gh.assess_cycles(edges)) == 1

    # Ребро relates цикла не замыкает.
    relates = [_edge(T1, T2), _edge(T2, T3), _edge(T3, T1, kind="relates")]
    assert gh.assess_cycles(relates) == []

    tree = ast.parse(Path(gh.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module, alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("cod_doc.services.plan_service.audit", "_find_cycles") in imported
    own_defs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_find_cycles" not in own_defs


def test_edge_rules_literals() -> None:
    # TA-002 зависит от блокера TA-001.
    a, b = (1, "TA-001"), (2, "TA-002")

    assert _codes(gh.assess_edges([_edge(b, a, note=None)], now=NOW)) == {
        ("edge_no_note", "TA-002->TA-001:blocks")
    }
    assert _codes(gh.assess_edges([_edge(b, a, note="  ")], now=NOW)) == {
        ("edge_no_note", "TA-002->TA-001:blocks")
    }
    # У закрытой зависимой задачи немое ребро ничего не держит — молчим.
    for closed in ("done", "cancelled"):
        assert gh.assess_edges([_edge(b, a, note=None, from_status=closed)], now=NOW) == []

    dead = _edge(b, a, to_status="done", to_completed_at=NOW - timedelta(days=31))
    assert _codes(gh.assess_edges([dead], now=NOW)) == {("edge_dead", "TA-002->TA-001:blocks")}
    # Легаси-написание pending — тот же todo.
    dead_pending = dead._replace(from_status="pending")
    assert _codes(gh.assess_edges([dead_pending], now=NOW)) == {
        ("edge_dead", "TA-002->TA-001:blocks")
    }
    # naive datetime — это UTC.
    dead_naive = dead._replace(to_completed_at=datetime(2026, 8, 27, 11, 0))
    assert _codes(gh.assess_edges([dead_naive], now=NOW)) == {
        ("edge_dead", "TA-002->TA-001:blocks")
    }

    fresh = dead._replace(to_completed_at=NOW - timedelta(days=29))
    assert gh.assess_edges([fresh], now=NOW) == []
    both_done = dead._replace(from_status="done")
    assert gh.assess_edges([both_done], now=NOW) == []

    cross = _edge(b, a, from_plan=1, to_plan=2)
    assert _codes(gh.assess_edges([cross], now=NOW)) == {
        ("edge_cross_plan", "TA-002->TA-001:blocks")
    }

    # Одно ребро — несколько находок разных кодов.
    everything = dead._replace(note=None, to_plan_id=2)
    assert _codes(gh.assess_edges([everything], now=NOW)) == {
        ("edge_no_note", "TA-002->TA-001:blocks"),
        ("edge_dead", "TA-002->TA-001:blocks"),
        ("edge_cross_plan", "TA-002->TA-001:blocks"),
    }

    assert gh.assess_edges([_edge(b, a)], now=NOW) == []


def test_section_rules_literals() -> None:
    broken = [
        SectionRow("p-plan", "A", "A-Data-Core", -1),
        SectionRow("p-plan", "B", "B-Services", 0),
        SectionRow("p-plan", "C", "C-Api", 0),
    ]
    assert _codes(gh.assess_sections(broken)) == {("section_positions", "p-plan")}

    sound = [
        SectionRow("q-plan", "A", "A-Data-Core", 0),
        SectionRow("q-plan", "B", "B-Services", 1),
        SectionRow("q-plan", "C", "C-Api", 2),
    ]
    assert gh.assess_sections(sound) == []

    gap = [SectionRow("r-plan", "A", "A-Data-Core", 0), SectionRow("r-plan", "B", "B-X", 2)]
    assert _codes(gh.assess_sections(gap)) == {("section_positions", "r-plan")}

    legacy = [SectionRow("s-plan", "D", "Structure protocol (RFC 24)", 0)]
    assert _codes(gh.assess_sections(legacy)) == {("section_slug", "s-plan:D")}


# ------------------------------------------------------------------ #
# Сборка на БД                                                        #
# ------------------------------------------------------------------ #


@pytest.fixture
def session_factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _project(session: Session, slug: str) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _plan(session: Session, pid: int, scope: str) -> tuple[int, int]:
    plan = plan_service.create_plan(
        session, project_id=pid, scope=scope, principle=None, author=AUTHOR
    )
    section = plan_service.create_section(
        session, project_id=pid, plan_scope=scope, letter="A", title="Data Core", author=AUTHOR
    )
    assert plan.row_id is not None
    assert section.row_id is not None
    return plan.row_id, section.row_id


def _task(session: Session, pid: int, plan_sec: tuple[int, int], task_id: str) -> int:
    task = task_service.create(
        session,
        project_id=pid,
        plan_id=plan_sec[0],
        section_id=plan_sec[1],
        task_id=task_id,
        title=f"Задача {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author=AUTHOR,
    )
    assert task.row_id is not None
    return task.row_id


def _raw_edge(session: Session, frm: int, to: int, note: str | None = None) -> None:
    session.add(DependencyModel(from_task_id=frm, to_task_id=to, kind="blocks", note=note))
    session.flush()


def _findings(session: Session, pid: int) -> list[tuple[str | None, str | None, str, int]]:
    return [
        (r.source_ref, r.kind, r.status, r.times_seen)
        for r in session.execute(
            select(FindingModel).where(FindingModel.project_id == pid).order_by(FindingModel.kind)
        ).scalars()
    ]


def _seed_problems(session: Session) -> int:
    """Проект с циклом, немым ребром и легаси-секцией."""
    pid = _project(session, "alpha")
    ps = _plan(session, pid, "alpha-plan")
    t1 = _task(session, pid, ps, "TA-001")
    t2 = _task(session, pid, ps, "TA-002")
    t3 = _task(session, pid, ps, "TA-003")
    _raw_edge(session, t1, t2, note="n")
    _raw_edge(session, t2, t3, note="n")
    _raw_edge(session, t3, t1, note=None)
    session.add(
        PlanSectionModel(
            plan_id=ps[0],
            letter="B",
            title="Structure protocol",
            slug="Structure protocol (RFC 24)",
            position=-1,
        )
    )
    session.flush()
    return pid


def test_assess_on_db_finds_seeded_problems(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_problems(session)
        assert _codes(gh.assess(session, pid, now=NOW)) == {
            ("cycle", "TA-001→TA-002→TA-003"),
            ("edge_no_note", "TA-003->TA-001:blocks"),
            ("section_positions", "alpha-plan"),
            ("section_slug", "alpha-plan:B"),
        }


def test_dead_and_cross_plan_on_db(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _project(session, "alpha")
        p1 = _plan(session, pid, "alpha-one")
        p2 = _plan(session, pid, "alpha-two")
        blocker = _task(session, pid, p1, "TA-001")
        _task(session, pid, p2, "TA-002")
        task_service.add_dependency(
            session,
            project_id=pid,
            task_id="TA-002",
            blocker_task_id="TA-001",
            note="нужна схема",
            author=AUTHOR,
        )
        row = session.get(TaskModel, blocker)
        assert row is not None
        row.status = "done"
        row.completed_at = NOW - timedelta(days=31)
        session.flush()

        assert _codes(gh.assess(session, pid, now=NOW)) == {
            ("edge_dead", "TA-002->TA-001:blocks"),
            ("edge_cross_plan", "TA-002->TA-001:blocks"),
        }


def test_empty_plan_is_silent(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        bare = _project(session, "bare")
        assert gh.assess(session, bare, now=NOW) == []
        gh.sync(session, project_id=bare, project_slug="bare", now=NOW)

        empty = _project(session, "empty")
        plan_service.create_plan(
            session, project_id=empty, scope="empty-plan", principle=None, author=AUTHOR
        )
        assert gh.assess(session, empty, now=NOW) == []
        gh.sync(session, project_id=empty, project_slug="empty", now=NOW)
        session.flush()

        count = session.query(FindingModel).count()
        assert count == 0


def test_sync_repeat_bumps_times_seen(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_problems(session)
        gh.sync(session, project_id=pid, project_slug="alpha", now=NOW)
        session.flush()
        first = _findings(session, pid)
        gh.sync(session, project_id=pid, project_slug="alpha", now=NOW)
        session.flush()
        second = _findings(session, pid)

        assert len(first) == 4
        assert len(second) == 4
        assert [r[3] for r in first] == [1, 1, 1, 1]
        assert [r[3] for r in second] == [2, 2, 2, 2]


def test_resolved_then_reopened(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _project(session, "alpha")
        ps = _plan(session, pid, "alpha-plan")
        t1 = _task(session, pid, ps, "TA-001")
        t2 = _task(session, pid, ps, "TA-002")
        _raw_edge(session, t2, t1, note=None)

        gh.sync(session, project_id=pid, project_slug="alpha", now=NOW)
        session.flush()
        assert _findings(session, pid) == [("graph_health", "edge_no_note", "open", 1)]
        row_id = session.execute(select(FindingModel.row_id)).scalar_one()

        edge = session.execute(select(DependencyModel)).scalar_one()
        edge.note = "мотивация"
        session.flush()
        gh.sync(session, project_id=pid, project_slug="alpha", now=NOW)
        session.flush()
        assert _findings(session, pid) == [("graph_health", "edge_no_note", "resolved", 1)]

        edge.note = ""
        session.flush()
        gh.sync(session, project_id=pid, project_slug="alpha", now=NOW)
        session.flush()
        rows = session.execute(select(FindingModel.row_id, FindingModel.status)).all()
        assert [tuple(r) for r in rows] == [(row_id, "open")]


def test_partition_is_graph_health(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_problems(session)
        fingerprint, _ = finding_service.fingerprint_routine(
            check_name="NODE-THIN", scope_kind="doc_node", scope_id="entry"
        )
        finding_service.ingest_findings(
            session,
            project_id=pid,
            source_run_id="doc_node_health:test",
            seeds=[
                finding_service.FindingSeed(
                    fingerprint=fingerprint,
                    source="routine",
                    source_ref="doc_node_health",
                    kind="NODE-THIN",
                    title="чужая",
                    severity="minor",
                )
            ],
        )
        session.flush()

        gh.sync(session, project_id=pid, project_slug="alpha", now=NOW)
        session.flush()

        refs = {
            r.kind: (r.source_ref, r.status)
            for r in session.execute(select(FindingModel)).scalars()
        }
        assert refs == {
            "NODE-THIN": ("doc_node_health", "open"),
            "cycle": ("graph_health", "open"),
            "edge_no_note": ("graph_health", "open"),
            "section_positions": ("graph_health", "open"),
            "section_slug": ("graph_health", "open"),
        }


def test_assess_is_project_scoped(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        alpha = _project(session, "alpha")
        pa = _plan(session, alpha, "alpha-plan")
        a1 = _task(session, alpha, pa, "TA-001")
        a2 = _task(session, alpha, pa, "TA-002")
        _raw_edge(session, a2, a1, note="есть")

        beta = _project(session, "beta")
        pb = _plan(session, beta, "beta-plan")
        b1 = _task(session, beta, pb, "TB-001")
        b2 = _task(session, beta, pb, "TB-002")
        _raw_edge(session, b2, b1, note=None)

        assert gh.assess(session, alpha, now=NOW) == []
        assert _codes(gh.assess(session, beta, now=NOW)) == {
            ("edge_no_note", "TB-002->TB-001:blocks")
        }


def test_module_not_in_plan_service_and_docstring() -> None:
    module_path = Path(gh.__file__).resolve()
    plan_service_dir = Path(plan_service.__file__).resolve().parent
    assert plan_service_dir not in module_path.parents
    assert module_path.parent.name == "services"
    doc = gh.__doc__ or ""
    assert "plan_service" in doc
    assert "паритет" in doc
