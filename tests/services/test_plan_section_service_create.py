"""ADO-201 (RFC 26 §3.1): write-путь планов и секций пишет ревизию и событие.

Эталоны — литералы и прямые SELECT по таблицам, а не вызовы кода под тестом.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

import cod_doc.services.plan_service as plan_pkg
from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
)
from cod_doc.services import revision_service
from cod_doc.services.plan_service import sections as svc
from cod_doc.services.plan_service._types import PlanNotFoundError
from cod_doc.services.validation import ValidationError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


SECTIONS_PY = (
    Path(__file__).resolve().parents[2] / "cod_doc" / "services" / "plan_service" / "sections.py"
)


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


def _count(session: Session, model: type, *where: object) -> int:
    stmt = select(func.count()).select_from(model)
    for cond in where:
        stmt = stmt.where(cond)  # type: ignore[arg-type]
    return int(session.execute(stmt).scalar_one())


def _audit_counts(sf: sessionmaker[Session]) -> tuple[int, int]:
    with transactional(sf) as s:
        return _count(s, RevisionModel), _count(s, ActivityEventModel)


def _make_plan(sf: sessionmaker[Session], project_id: int, scope: str = "plan-x") -> None:
    with transactional(sf) as s:
        svc.create_plan(s, project_id=project_id, scope=scope, principle="from-rfc", author="t")


def test_entity_kind_plan_section_value() -> None:
    assert EntityKind.PLAN_SECTION.value == "plan_section"
    assert EntityKind("plan_section") is EntityKind.PLAN_SECTION


def test_create_plan_writes_revision_and_event(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, _ = projects
    _make_plan(sf, alpha)

    with transactional(sf) as s:
        plans = s.execute(select(PlanModel)).scalars().all()
        assert len(plans) == 1
        assert plans[0].scope == "plan-x"
        assert plans[0].project_id == alpha
        plan_id = plans[0].row_id

        revs = s.execute(select(RevisionModel)).scalars().all()
        assert len(revs) == 1
        assert revs[0].entity_kind == "plan"
        assert revs[0].entity_id == plan_id

        events = s.execute(select(ActivityEventModel)).scalars().all()
        assert len(events) == 1
        assert events[0].kind == "plan.created"


def test_create_plan_duplicate_scope_raises(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, beta = projects
    _make_plan(sf, alpha)
    assert _audit_counts(sf) == (1, 1)

    for project_id in (alpha, beta):
        with pytest.raises(svc.PlanAlreadyExistsError), transactional(sf) as s:
            svc.create_plan(s, project_id=project_id, scope="plan-x", principle=None, author="t")

    assert _audit_counts(sf) == (1, 1)
    with transactional(sf) as s:
        assert _count(s, PlanModel) == 1


def test_create_section_writes_revision_and_event(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, _ = projects
    _make_plan(sf, alpha)

    with transactional(sf) as s:
        sec = svc.create_section(
            s,
            project_id=alpha,
            plan_scope="plan-x",
            letter="f",
            title="Structure protocol (RFC 24)",
            author="t",
        )
        sec_id = sec.row_id

    with transactional(sf) as s:
        row = s.execute(select(PlanSectionModel)).scalar_one()
        assert row.row_id == sec_id
        assert row.letter == "F"
        assert row.slug == "F-Structure-protocol-RFC-24"
        assert row.position == 0

        revs = (
            s.execute(select(RevisionModel).where(RevisionModel.entity_kind == "plan_section"))
            .scalars()
            .all()
        )
        assert len(revs) == 1
        assert revs[0].entity_id == sec_id

        events = (
            s.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "plan.section_created")
            )
            .scalars()
            .all()
        )
        assert len(events) == 1

    with transactional(sf) as s:
        svc.create_section(
            s, project_id=alpha, plan_scope="plan-x", letter="G", title="Next", author="t"
        )
    with transactional(sf) as s:
        second = s.execute(
            select(PlanSectionModel).where(PlanSectionModel.letter == "G")
        ).scalar_one()
        assert second.position == 1


def test_create_section_reason_optional(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, _ = projects
    _make_plan(sf, alpha)
    with transactional(sf) as s:
        svc.create_section(
            s, project_id=alpha, plan_scope="plan-x", letter="A", title="Alpha", author="t"
        )
    with transactional(sf) as s:
        assert _count(s, PlanSectionModel) == 1

    assert inspect.signature(svc.create_plan).parameters["reason"].default is None
    assert inspect.signature(svc.create_section).parameters["reason"].default is None


def test_create_section_duplicate_letter_raises(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, _ = projects
    _make_plan(sf, alpha)
    with transactional(sf) as s:
        svc.create_section(
            s, project_id=alpha, plan_scope="plan-x", letter="F", title="Eff", author="t"
        )
    assert _audit_counts(sf) == (2, 2)

    for letter in ("F", "f"):
        with pytest.raises(svc.SectionAlreadyExistsError), transactional(sf) as s:
            svc.create_section(
                s, project_id=alpha, plan_scope="plan-x", letter=letter, title="Dup", author="t"
            )
        assert _audit_counts(sf) == (2, 2)

    bad: list[dict[str, object]] = [
        {"letter": "G", "title": ""},
        {"letter": "ABC", "title": "Too long"},
        {"letter": "H", "title": "Negative", "position": -1},
    ]
    for kwargs in bad:
        with pytest.raises(ValidationError), transactional(sf) as s:
            svc.create_section(
                s,
                project_id=alpha,
                plan_scope="plan-x",
                author="t",
                **kwargs,  # type: ignore[arg-type]
            )
        assert _audit_counts(sf) == (2, 2)

    with transactional(sf) as s:
        assert _count(s, PlanSectionModel) == 1


def test_resolver_checks_project_id(sf: sessionmaker[Session], projects: tuple[int, int]) -> None:
    alpha, beta = projects
    _make_plan(sf, alpha)

    with pytest.raises(PlanNotFoundError), transactional(sf) as s:
        svc.create_section(
            s, project_id=beta, plan_scope="plan-x", letter="A", title="Alien", author="t"
        )
    with pytest.raises(PlanNotFoundError), transactional(sf) as s:
        svc.create_section(
            s, project_id=alpha, plan_scope="no-such-plan", letter="A", title="Ghost", author="t"
        )

    with transactional(sf) as s:
        assert _count(s, PlanSectionModel) == 0


def test_write_is_atomic_with_revision_and_event(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, _ = projects
    _make_plan(sf, alpha)

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom), transactional(sf) as s:
        svc.create_section(
            s, project_id=alpha, plan_scope="plan-x", letter="A", title="Alpha", author="t"
        )
        raise Boom

    with transactional(sf) as s:
        assert _count(s, PlanSectionModel) == 0
        assert _count(s, RevisionModel, RevisionModel.entity_kind == "plan_section") == 0
        assert _count(s, ActivityEventModel, ActivityEventModel.kind == "plan.section_created") == 0


def test_revert_plan_section_not_supported(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    """Решение, а не случайность: ревизии секций плана автоматически не откатываются."""
    alpha, _ = projects
    _make_plan(sf, alpha)
    with transactional(sf) as s:
        svc.create_section(
            s, project_id=alpha, plan_scope="plan-x", letter="A", title="Alpha", author="t"
        )
    with transactional(sf) as s:
        revision_id = s.execute(
            select(RevisionModel.revision_id).where(RevisionModel.entity_kind == "plan_section")
        ).scalar_one()

    with pytest.raises(revision_service.RevertNotSupportedError), transactional(sf) as s:
        revision_service.revert(s, revision_id, author="t")


def test_diff_is_module_local() -> None:
    tree = ast.parse(SECTIONS_PY.read_text(encoding="utf-8"))
    top_level_defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "_diff" in top_level_defs
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "_diff" not in imported


def test_package_reexports_write_functions() -> None:
    for name in (
        "create_plan",
        "create_section",
        "update_section",
        "move_section",
        "delete_section",
    ):
        assert hasattr(plan_pkg, name)
        assert name in plan_pkg.__all__
    assert "Pure read-side service" not in (plan_pkg.__doc__ or "")
