"""ADO-201 (RFC 26 §3.1): move_section даёт плотный порядок 0..n-1, delete_section
удаляет непустую секцию только через reassign_to.

Эталоны — литералы и прямые SELECT по таблицам, а не вызовы кода под тестом.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
    TaskModel,
)
from cod_doc.services import task_service
from cod_doc.services.plan_service import sections as svc
from cod_doc.services.plan_service._types import PlanNotFoundError
from cod_doc.services.validation import ValidationError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


def _project(session: Session, slug: str) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


@pytest.fixture
def projects(sf: sessionmaker[Session]) -> tuple[int, int]:
    with transactional(sf) as s:
        return _project(s, "alpha"), _project(s, "beta")


@pytest.fixture
def abcd(sf: sessionmaker[Session], projects: tuple[int, int]) -> tuple[int, int]:
    """План plan-x в alpha с секциями A, B, C, D на позициях 0..3."""
    alpha, _ = projects
    with transactional(sf) as s:
        svc.create_plan(s, project_id=alpha, scope="plan-x", principle=None, author="t")
        for letter in "ABCD":
            svc.create_section(
                s,
                project_id=alpha,
                plan_scope="plan-x",
                letter=letter,
                title=f"Section {letter}",
                author="t",
            )
    return projects


@pytest.fixture
def b_has_two_tasks(sf: sessionmaker[Session], abcd: tuple[int, int]) -> tuple[int, int]:
    alpha, _ = abcd
    with transactional(sf) as s:
        plan_id = s.execute(select(PlanModel.row_id)).scalar_one()
        b_id = _section_id(s, "B")
        for tid in ("PX-001", "PX-002"):
            task_service.create(
                s,
                project_id=alpha,
                plan_id=plan_id,
                section_id=b_id,
                task_id=tid,
                title=f"Task {tid}",
                type=TaskType.FEATURE,
                priority=Priority.MEDIUM,
                author="t",
            )
    return abcd


def _section_id(session: Session, letter: str) -> int:
    return int(
        session.execute(
            select(PlanSectionModel.row_id).where(PlanSectionModel.letter == letter)
        ).scalar_one()
    )


def _db_order(sf: sessionmaker[Session]) -> list[tuple[str, int]]:
    with transactional(sf) as s:
        rows = s.execute(
            select(PlanSectionModel.letter, PlanSectionModel.position).order_by(
                PlanSectionModel.letter
            )
        ).all()
        return [(r.letter, r.position) for r in rows]


def _counts(sf: sessionmaker[Session]) -> tuple[int, int]:
    with transactional(sf) as s:
        revs = int(s.execute(select(func.count()).select_from(RevisionModel)).scalar_one())
        events = int(s.execute(select(func.count()).select_from(ActivityEventModel)).scalar_one())
        return revs, events


def _move(sf: sessionmaker[Session], project_id: int, letter: str, **kwargs: object) -> list:  # type: ignore[type-arg]
    with transactional(sf) as s:
        result = svc.move_section(
            s,
            project_id=project_id,
            plan_scope="plan-x",
            letter=letter,
            author="t",
            reason="reorder",
            **kwargs,  # type: ignore[arg-type]
        )
        return [(sec.letter, sec.position) for sec in result]


def _delete(sf: sessionmaker[Session], project_id: int, letter: str, **kwargs: object) -> int:
    with transactional(sf) as s:
        return svc.delete_section(
            s,
            project_id=project_id,
            plan_scope="plan-x",
            letter=letter,
            author="t",
            reason="cleanup",
            **kwargs,  # type: ignore[arg-type]
        )


def test_move_before_after_position(sf: sessionmaker[Session], abcd: tuple[int, int]) -> None:
    alpha, _ = abcd

    assert _move(sf, alpha, "D", before="B") == [("A", 0), ("D", 1), ("B", 2), ("C", 3)]
    assert _db_order(sf) == [("A", 0), ("B", 2), ("C", 3), ("D", 1)]

    assert _move(sf, alpha, "A", after="C") == [("D", 0), ("B", 1), ("C", 2), ("A", 3)]
    assert _db_order(sf) == [("A", 3), ("B", 1), ("C", 2), ("D", 0)]

    assert _move(sf, alpha, "B", position=0) == [("B", 0), ("D", 1), ("C", 2), ("A", 3)]
    assert _db_order(sf) == [("A", 3), ("B", 0), ("C", 2), ("D", 1)]


def test_move_normalizes_negative_positions(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, _ = projects
    with transactional(sf) as s:
        svc.create_plan(s, project_id=alpha, scope="plan-x", principle=None, author="t")
        plan_id = s.execute(select(PlanModel.row_id)).scalar_one()
        for letter, pos in (("X", -1), ("A", 0), ("B", 0), ("C", 5)):
            s.add(
                PlanSectionModel(
                    plan_id=plan_id,
                    letter=letter,
                    title=f"Legacy {letter}",
                    slug=f"{letter}-Legacy",
                    position=pos,
                )
            )
            s.flush()

    assert _move(sf, alpha, "C", position=0) == [("C", 0), ("X", 1), ("A", 2), ("B", 3)]
    assert _db_order(sf) == [("A", 2), ("B", 3), ("C", 0), ("X", 1)]


def test_move_writes_revision_per_changed_section_and_one_event(
    sf: sessionmaker[Session], abcd: tuple[int, int]
) -> None:
    alpha, _ = abcd
    before_revs, before_events = _counts(sf)

    _move(sf, alpha, "D", before="B")

    assert _counts(sf) == (before_revs + 3, before_events + 1)
    with transactional(sf) as s:
        ids = {letter: _section_id(s, letter) for letter in "ABCD"}
        revs = (
            s.execute(
                select(RevisionModel)
                .where(RevisionModel.entity_kind == "plan_section")
                .order_by(RevisionModel.row_id)
            )
            .scalars()
            .all()
        )
        move_revs = revs[4:]
        assert len(move_revs) == 3
        assert {r.entity_id for r in move_revs} == {ids["D"], ids["B"], ids["C"]}
        by_letter = {json.loads(r.diff)["letter"]: json.loads(r.diff) for r in move_revs}
        assert by_letter["D"]["position"] == {"from": 3, "to": 1}
        assert by_letter["B"]["position"] == {"from": 1, "to": 2}
        assert by_letter["C"]["position"] == {"from": 2, "to": 3}
        assert {d["moved"] for d in by_letter.values()} == {"D"}
        assert {d["op"] for d in by_letter.values()} == {"move_section"}
        assert {r.reason for r in move_revs} == {"reorder"}

        events = (
            s.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "plan.section_moved")
            )
            .scalars()
            .all()
        )
        assert len(events) == 1


def test_move_noop_writes_nothing(sf: sessionmaker[Session], abcd: tuple[int, int]) -> None:
    alpha, _ = abcd
    before = _counts(sf)

    assert _move(sf, alpha, "A", position=0) == [("A", 0), ("B", 1), ("C", 2), ("D", 3)]

    after = _counts(sf)
    assert (after[0] - before[0], after[1] - before[1]) == (0, 0)


def test_move_argument_rules(sf: sessionmaker[Session], abcd: tuple[int, int]) -> None:
    alpha, _ = abcd

    with pytest.raises(ValueError, match="exactly one"):
        _move(sf, alpha, "A")
    with pytest.raises(ValueError, match="exactly one"):
        _move(sf, alpha, "A", before="B", position=1)
    with pytest.raises(ValueError, match="itself"):
        _move(sf, alpha, "A", before="A")
    with pytest.raises(svc.SectionNotFoundError):
        _move(sf, alpha, "A", before="Z")
    with pytest.raises(ValidationError) as exc:
        _move(sf, alpha, "A", position=-1)
    assert exc.value.code == "PS-003"
    with pytest.raises(ValueError, match="reason"), transactional(sf) as s:
        svc.move_section(
            s, project_id=alpha, plan_scope="plan-x", letter="A", author="t", reason=" ", position=1
        )

    assert _move(sf, alpha, "A", position=99) == [("B", 0), ("C", 1), ("D", 2), ("A", 3)]


def test_delete_empty_section(sf: sessionmaker[Session], abcd: tuple[int, int]) -> None:
    alpha, _ = abcd
    with transactional(sf) as s:
        d_id = _section_id(s, "D")
    before_revs, before_events = _counts(sf)

    assert _delete(sf, alpha, "D") == 0

    assert _counts(sf) == (before_revs + 1, before_events + 1)
    with transactional(sf) as s:
        gone = s.execute(
            select(func.count())
            .select_from(PlanSectionModel)
            .where(PlanSectionModel.row_id == d_id)
        ).scalar_one()
        assert gone == 0
        revs = (
            s.execute(
                select(RevisionModel).where(
                    RevisionModel.entity_kind == "plan_section", RevisionModel.entity_id == d_id
                )
            )
            .scalars()
            .all()
        )
        assert len(revs) == 2  # create + delete
        diff = json.loads(revs[-1].diff)
        assert diff["op"] == "delete_section"
        assert diff["letter"] == "D"
        assert diff["position"] == 3
        assert diff["moved_task_ids"] == []
        events = (
            s.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "plan.section_deleted")
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
    assert _db_order(sf) == [("A", 0), ("B", 1), ("C", 2)]


def test_delete_non_empty_requires_reassign(
    sf: sessionmaker[Session], b_has_two_tasks: tuple[int, int]
) -> None:
    alpha, _ = b_has_two_tasks
    before = _counts(sf)

    with pytest.raises(svc.SectionHasTasksError) as exc:
        _delete(sf, alpha, "B")
    assert exc.value.task_count == 2

    assert _counts(sf) == before
    with transactional(sf) as s:
        b_id = _section_id(s, "B")
        section_ids = s.execute(select(TaskModel.section_id).order_by(TaskModel.task_id)).all()
        assert [r.section_id for r in section_ids] == [b_id, b_id]


def test_delete_with_reassign_moves_tasks(
    sf: sessionmaker[Session], b_has_two_tasks: tuple[int, int]
) -> None:
    alpha, _ = b_has_two_tasks
    with transactional(sf) as s:
        b_id = _section_id(s, "B")
        c_id = _section_id(s, "C")
        plan_id = s.execute(select(PlanModel.row_id)).scalar_one()

    assert _delete(sf, alpha, "B", reassign_to="C") == 2

    with transactional(sf) as s:
        rows = s.execute(
            select(TaskModel.task_id, TaskModel.section_id, TaskModel.plan_id).order_by(
                TaskModel.task_id
            )
        ).all()
        assert [tuple(r) for r in rows] == [
            ("PX-001", c_id, plan_id),
            ("PX-002", c_id, plan_id),
        ]
        rev = (
            s.execute(
                select(RevisionModel)
                .where(RevisionModel.entity_kind == "plan_section", RevisionModel.entity_id == b_id)
                .order_by(RevisionModel.row_id.desc())
            )
            .scalars()
            .first()
        )
        assert rev is not None
        diff = json.loads(rev.diff)
        assert diff["moved_task_ids"] == ["PX-001", "PX-002"]
        assert diff["reassign_to"] == "C"
        assert rev.reason == "cleanup"
    assert _db_order(sf) == [("A", 0), ("C", 2), ("D", 3)]


def test_delete_has_no_force_flag() -> None:
    delete_params = inspect.signature(svc.delete_section).parameters
    move_params = inspect.signature(svc.move_section).parameters
    assert "force" not in delete_params
    for params in (delete_params, move_params):
        assert params["reason"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["reason"].default is inspect.Parameter.empty


def test_reassign_rules(sf: sessionmaker[Session], b_has_two_tasks: tuple[int, int]) -> None:
    alpha, beta = b_has_two_tasks

    with pytest.raises(ValueError, match="itself"):
        _delete(sf, alpha, "B", reassign_to="B")
    with pytest.raises(svc.SectionNotFoundError):
        _delete(sf, alpha, "B", reassign_to="Z")
    with pytest.raises(PlanNotFoundError):
        _delete(sf, beta, "B", reassign_to="C")
    with pytest.raises(ValueError, match="reason"), transactional(sf) as s:
        svc.delete_section(
            s, project_id=alpha, plan_scope="plan-x", letter="D", author="t", reason=""
        )

    assert _db_order(sf) == [("A", 0), ("B", 1), ("C", 2), ("D", 3)]
