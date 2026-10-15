"""CLO-004: вставка и перемещение пишут ранг одной строки (RFC 29 §3.2, Q-009).

Сверка по каждому аллокатору: снимок ``{row_id: rank}`` родителя до и после
вызова отличается ровно одной строкой — новой или перемещённой; ``position``
там, где он индекс, пересчитан из ранга. И SCN-093: две реплики, вставившие по
шагу в один сценарий, после обмена строками читают одинаковый порядок без
единой правки ранга у старых шагов.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType, ScenarioKind
from cod_doc.infra import ordering
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import (
    ADRTopicModel,
    DocNodeModel,
    DocumentModel,
    PlanSectionModel,
    ProjectModel,
    ScenarioStepModel,
    SectionModel,
)
from cod_doc.infra.repositories import ScenarioStepRepository
from cod_doc.services import (
    adr_topic_service,
    doc_service,
    doc_tree_service,
    plan_service,
    scenario_service,
)
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Engine
    from sqlalchemy.orm import Session

AUTHOR = "human:test"


def _project(session: Session, slug: str = "rk") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title="P", root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _ranks(session: Session, model: type, **parent: object) -> dict[int, str | None]:
    return {r.row_id: r.rank for r in ordering.siblings(session, model, **parent)}


def _one_changed(before: dict[int, str | None], after: dict[int, str | None]) -> int:
    changed = [k for k in after if before.get(k, "<new>") != after[k]]
    assert len(changed) == 1, f"переписано рангов: {len(changed)} ({changed})"
    assert all(after[k] == v for k, v in before.items() if k in after and k != changed[0])
    return changed[0]


def _doc(session: Session, pid: int, anchors: list[str]) -> int:
    doc = doc_service.create(
        session,
        project_id=pid,
        doc_key="guide",
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        title="Guide",
        owner="t",
        author=AUTHOR,
    )
    assert doc.row_id is not None
    for i, anchor in enumerate(anchors):
        doc_service.add_section(
            session,
            project_id=pid,
            document_id=doc.row_id,
            anchor=anchor,
            heading=anchor,
            level=2,
            position=i,
            body=f"## {anchor}\n",
            author=AUTHOR,
        )
    return doc.row_id


def test_section_insert_between_writes_one_rank(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session)
        doc_id = _doc(session, pid, ["a", "b", "c"])
        before = _ranks(session, SectionModel, document_id=doc_id)
        # position=0 — ничья с «a»: новая встаёт после неё, как раньше по row_id.
        new = doc_service.add_section(
            session,
            project_id=pid,
            document_id=doc_id,
            anchor="x",
            heading="x",
            level=2,
            position=0,
            body="## x\n",
            author=AUTHOR,
        )
        after = _ranks(session, SectionModel, document_id=doc_id)
        assert _one_changed(before, after) == new.row_id
        rows = ordering.siblings(session, SectionModel, document_id=doc_id)
        assert [r.anchor for r in rows] == ["a", "x", "b", "c"]


def test_section_move_writes_one_rank_and_renumbers(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session)
        doc_id = _doc(session, pid, ["a", "b", "c", "d"])
        before = _ranks(session, SectionModel, document_id=doc_id)
        doc_service.move_section(
            session, project_id=pid, document_id=doc_id, anchor="d", position=1, author=AUTHOR
        )
        after = _ranks(session, SectionModel, document_id=doc_id)
        moved = _one_changed(before, after)
        rows = ordering.siblings(session, SectionModel, document_id=doc_id)
        assert [r.anchor for r in rows] == ["a", "d", "b", "c"]
        assert [r.position for r in rows] == [0, 1, 2, 3]
        assert rows[1].row_id == moved


def test_plan_section_create_and_move(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session)
        plan = plan_service.create_plan(
            session, project_id=pid, scope="p", principle=None, author=AUTHOR
        )
        for letter in "ABC":
            plan_service.create_section(
                session, project_id=pid, plan_scope="p", letter=letter, title=letter, author=AUTHOR
            )
        before = _ranks(session, PlanSectionModel, plan_id=plan.row_id)
        plan_service.create_section(
            session, project_id=pid, plan_scope="p", letter="D", title="D", author=AUTHOR,
            position=1,
        )  # fmt: skip
        mid = _ranks(session, PlanSectionModel, plan_id=plan.row_id)
        _one_changed(before, mid)
        rows = ordering.siblings(session, PlanSectionModel, plan_id=plan.row_id)
        assert [r.letter for r in rows] == ["A", "B", "D", "C"]  # ничья с B → после B

        plan_service.move_section(
            session, project_id=pid, plan_scope="p", letter="C", author=AUTHOR, reason="t",
            before="A",
        )  # fmt: skip
        _one_changed(mid, _ranks(session, PlanSectionModel, plan_id=plan.row_id))
        rows = ordering.siblings(session, PlanSectionModel, plan_id=plan.row_id)
        assert [r.letter for r in rows] == ["C", "A", "B", "D"]
        assert [r.position for r in rows] == [0, 1, 2, 3]


def test_doc_node_create_update_and_assign(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session)
        for key in ("a", "b", "c"):
            doc_tree_service.create_node(
                session, project_id=pid, node_key=key, title=key, author=AUTHOR
            )
        before = _ranks(session, DocNodeModel, project_id=pid)
        doc_tree_service.update_node(
            session, project_id=pid, node_key="c", position=0, author=AUTHOR
        )
        _one_changed(before, _ranks(session, DocNodeModel, project_id=pid))
        rows = ordering.siblings(session, DocNodeModel, project_id=pid)
        # Позиция раздела — advisory-число как задано; ничья (a=0, c=0) — по
        # node_key, как читал код до 0050.
        assert [(r.node_key, r.position) for r in rows] == [("a", 0), ("c", 0), ("b", 1)]

        _doc(session, pid, [])
        doc_tree_service.assign(
            session, project_id=pid, doc_key="guide", node_key="a", author=AUTHOR
        )
        doc = session.execute(select(DocumentModel)).scalar_one()
        assert doc.node_rank is not None
        doc_tree_service.assign(
            session, project_id=pid, doc_key="guide", node_key=None, author=AUTHOR
        )
        assert doc.node_rank is None, "Инбокс не упорядочен"


def test_adr_topic_move_writes_one_rank(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _project(session)
        for name in ("one", "two", "three"):
            adr_topic_service.create(session, project_id=pid, name=name)
        before = _ranks(session, ADRTopicModel, project_id=pid)
        adr_topic_service.move(session, project_id=pid, name="three", position=0)
        _one_changed(before, _ranks(session, ADRTopicModel, project_id=pid))
        rows = adr_topic_service.list_for_project(session, pid)
        assert [(t.name, t.position) for t in rows] == [("three", 0), ("one", 1), ("two", 2)]


# --- SCN-093 ----------------------------------------------------------------

_SCN_RANKS = ("a0", "b5", "c0", "c5", "d0")


def _replica(tmp_path: Path, name: str) -> Engine:
    url = f"sqlite:///{tmp_path / name}.db"
    run_alembic("upgrade", "head", db_url=url)
    return make_engine(url)


def _seed_scn081(session: Session) -> tuple[int, int]:
    """SCN-081 с пятью шагами на рангах a0, b5, c0, c5, d0 (предусловие SCN-093)."""
    pid = _project(session, "scn")
    scenario = scenario_service.create(
        session,
        project_id=pid,
        scenario_id="SCN-081",
        title="t",
        kind=ScenarioKind.HAPPY_PATH,
        group_key="g",
        preconditions="p",
        expected="e",
        steps=[str(i) for i in range(1, 6)],
        author=AUTHOR,
    )
    assert scenario.row_id is not None
    steps = ordering.siblings(session, ScenarioStepModel, scenario_row_id=scenario.row_id)
    for step, rank in zip(steps, _SCN_RANKS, strict=True):
        step.rank = rank
        step.uid = f"seed-{step.text}"
    session.flush()
    return pid, scenario.row_id


def _new_steps(session: Session, scenario_row_id: int) -> list[ScenarioStepModel]:
    rows = ordering.siblings(session, ScenarioStepModel, scenario_row_id=scenario_row_id)
    return [r for r in rows if r.uid is None or not r.uid.startswith("seed-")]


def test_scn_093_parallel_step_inserts_merge_by_rank(tmp_path: Path) -> None:
    """SCN-093: A вставляет шаг между 2 и 3, B дописывает в конец; обмен — по строке.

    Обмен строками стоит на месте pull/push (CLO-005+): переносится ровно то,
    что реплика записала, — новая строка с её рангом и uid.
    """
    engines = {"A": _replica(tmp_path, "a"), "B": _replica(tmp_path, "b")}
    factories = {k: make_session_factory(e) for k, e in engines.items()}
    ids: dict[str, tuple[int, int]] = {}
    for name, factory in factories.items():
        with transactional(factory) as session:
            ids[name] = _seed_scn081(session)

    inserted: dict[str, tuple[str, str]] = {}
    for name, text, position in (("A", "шаг A", 2), ("B", "шаг B", None)):
        pid, row_id = ids[name]
        with transactional(factories[name]) as session:
            scenario_service.add_step(
                session,
                project_id=pid,
                scenario_id="SCN-081",
                text=text,
                author=AUTHOR,
                position=position,
            )
            (new,) = _new_steps(session, row_id)
            new.uid = f"uid-{name}"
            assert new.rank is not None
            inserted[name] = (new.text, new.rank)
            old = ordering.siblings(session, ScenarioStepModel, scenario_row_id=row_id)
            assert [r.rank for r in old if r.uid and r.uid.startswith("seed-")] == list(
                _SCN_RANKS
            ), "старые шаги получили новый ранг"

    for name, other in (("A", "B"), ("B", "A")):
        text, rank = inserted[other]
        _, row_id = ids[name]
        with transactional(factories[name]) as session:
            session.add(
                ScenarioStepModel(
                    scenario_row_id=row_id, position=99, text=text, rank=rank, uid=f"uid-{other}"
                )
            )
            session.flush()
            ordering.renumber_parent(session, ScenarioStepModel, scenario_row_id=row_id)

    expected = ["1", "2", "шаг A", "3", "4", "5", "шаг B"]
    for name, factory in factories.items():
        _, row_id = ids[name]
        with transactional(factory) as session:
            steps = ScenarioStepRepository(session).list_for_scenario(row_id)
            assert [s.text for s in steps] == expected, name
            assert [s.position for s in steps] == list(range(7)), name
            old = [s.rank for s in steps if s.text in {"1", "2", "3", "4", "5"}]
            assert old == list(_SCN_RANKS), name
    for engine in engines.values():
        engine.dispose()


def test_add_step_out_of_range_is_refused(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid, _ = _seed_scn081(session)
        with pytest.raises(ValueError, match="out of range"):
            scenario_service.add_step(
                session, project_id=pid, scenario_id="SCN-081", text="x", author=AUTHOR,
                position=9,
            )  # fmt: skip
