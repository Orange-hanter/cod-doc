"""ADO-201 (RFC 26 §3.1): update_section — None не трогает поле, идемпотентный
вызов ничего не пишет, adopt берёт легаси-секцию в историю.

Эталоны — литералы и прямые SELECT по таблицам, а не вызовы кода под тестом.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    DocumentModel,
    PlanModel,
    PlanSectionModel,
    ProjectModel,
    RevisionModel,
)
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


def _document(session: Session, project_id: int, doc_key: str) -> int:
    now = datetime.now(UTC)
    model = DocumentModel(
        project_id=project_id,
        doc_key=doc_key,
        path=f"{doc_key}.md",
        type=DocumentType.MODULE_SPEC.value,
        status=DocumentStatus.DRAFT.value,
        title=doc_key,
        created=now,
        last_updated=now,
    )
    session.add(model)
    session.flush()
    return model.row_id


@pytest.fixture
def sf(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


@pytest.fixture
def projects(sf: sessionmaker[Session]) -> tuple[int, int]:
    with transactional(sf) as s:
        return _project(s, "alpha"), _project(s, "beta")


@pytest.fixture
def seeded(sf: sessionmaker[Session], projects: tuple[int, int]) -> tuple[int, int]:
    """План plan-x в alpha с секцией A «Alpha title», слаг A-Alpha-slug."""
    alpha, _ = projects
    with transactional(sf) as s:
        svc.create_plan(s, project_id=alpha, scope="plan-x", principle=None, author="t")
        svc.create_section(
            s,
            project_id=alpha,
            plan_scope="plan-x",
            letter="A",
            title="Alpha title",
            slug="A-Alpha-slug",
            author="t",
        )
    return projects


def _counts(sf: sessionmaker[Session]) -> tuple[int, int]:
    with transactional(sf) as s:
        revs = int(s.execute(select(func.count()).select_from(RevisionModel)).scalar_one())
        events = int(s.execute(select(func.count()).select_from(ActivityEventModel)).scalar_one())
        return revs, events


def _section_row(sf: sessionmaker[Session], letter: str = "A") -> tuple[object, ...]:
    with transactional(sf) as s:
        row = s.execute(
            select(PlanSectionModel).where(PlanSectionModel.letter == letter)
        ).scalar_one()
        return row.title, row.slug, row.position, row.doc_id


def _update(sf: sessionmaker[Session], project_id: int, **kwargs: object) -> None:
    with transactional(sf) as s:
        svc.update_section(
            s,
            project_id=project_id,
            plan_scope="plan-x",
            author="t",
            **{"letter": "A", "reason": "r", **kwargs},  # type: ignore[arg-type]
        )


def test_update_title_writes_one_revision_and_event(
    sf: sessionmaker[Session], seeded: tuple[int, int]
) -> None:
    alpha, _ = seeded
    assert _counts(sf) == (2, 2)

    _update(sf, alpha, title="New title")

    assert _section_row(sf) == ("New title", "A-Alpha-slug", 0, None)
    with transactional(sf) as s:
        section_id = s.execute(select(PlanSectionModel.row_id)).scalar_one()
        revs = (
            s.execute(select(RevisionModel).where(RevisionModel.entity_kind == "plan_section"))
            .scalars()
            .all()
        )
        assert len(revs) == 2  # create + update
        assert [r.entity_id for r in revs] == [section_id, section_id]
        events = (
            s.execute(
                select(ActivityEventModel).where(ActivityEventModel.kind == "plan.section_updated")
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
    assert _counts(sf) == (3, 3)


def test_none_means_untouched(sf: sessionmaker[Session], seeded: tuple[int, int]) -> None:
    alpha, _ = seeded
    _update(sf, alpha, slug="A-New-Slug")
    assert _section_row(sf) == ("Alpha title", "A-New-Slug", 0, None)


def test_idempotent_update_writes_nothing(
    sf: sessionmaker[Session], seeded: tuple[int, int]
) -> None:
    alpha, _ = seeded
    _update(sf, alpha, title="Alpha title", slug="A-Alpha-slug")
    _update(sf, alpha)
    _update(sf, alpha, letter="a")

    assert _counts(sf) == (2, 2)
    assert _section_row(sf) == ("Alpha title", "A-Alpha-slug", 0, None)


def test_adopt_writes_revision_without_changing_data(
    sf: sessionmaker[Session], projects: tuple[int, int]
) -> None:
    alpha, _ = projects
    with transactional(sf) as s:
        plan = PlanModel(project_id=alpha, scope="plan-x", principle=None)
        s.add(plan)
        s.flush()
        legacy = PlanSectionModel(
            plan_id=plan.row_id, letter="J", title="Legacy J", slug="J-Legacy", position=3
        )
        s.add(legacy)
        s.flush()
        legacy_id = legacy.row_id
    assert _counts(sf) == (0, 0)

    with transactional(sf) as s:
        svc.update_section(
            s,
            project_id=alpha,
            plan_scope="plan-x",
            letter="J",
            author="t",
            reason="adopt",
            adopt=True,
        )

    with transactional(sf) as s:
        revs = s.execute(select(RevisionModel)).scalars().all()
        assert len(revs) == 1
        assert revs[0].entity_kind == "plan_section"
        assert revs[0].entity_id == legacy_id
        events = s.execute(select(ActivityEventModel)).scalars().all()
        assert len(events) == 1
        assert events[0].kind == "plan.section_updated"
    assert _section_row(sf, "J") == ("Legacy J", "J-Legacy", 3, None)


def test_reason_is_required(sf: sessionmaker[Session], seeded: tuple[int, int]) -> None:
    param = inspect.signature(svc.update_section).parameters["reason"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty

    alpha, _ = seeded
    for reason in ("", "   "):
        with pytest.raises(ValueError, match="reason"):
            _update(sf, alpha, title="Other", reason=reason)
    assert _counts(sf) == (2, 2)
    assert _section_row(sf) == ("Alpha title", "A-Alpha-slug", 0, None)


def test_update_validates_input(sf: sessionmaker[Session], seeded: tuple[int, int]) -> None:
    alpha, beta = seeded
    with transactional(sf) as s:
        foreign_doc = _document(s, beta, "beta-doc")

    with pytest.raises(ValidationError) as exc:
        _update(sf, alpha, title="a\nb")
    assert exc.value.code == "PS-001"

    with pytest.raises(ValidationError) as exc:
        _update(sf, alpha, slug="bad slug")
    assert exc.value.code == "TP-003"

    with pytest.raises(ValueError, match="document"):
        _update(sf, alpha, doc_id=foreign_doc)

    assert _counts(sf) == (2, 2)
    assert _section_row(sf) == ("Alpha title", "A-Alpha-slug", 0, None)


def test_update_resolver_scoped(sf: sessionmaker[Session], seeded: tuple[int, int]) -> None:
    alpha, beta = seeded
    with pytest.raises(PlanNotFoundError):
        _update(sf, beta, title="Alien")
    with pytest.raises(svc.SectionNotFoundError):
        _update(sf, alpha, letter="Z", title="Ghost")
    assert _counts(sf) == (2, 2)


def test_doc_id_set(sf: sessionmaker[Session], seeded: tuple[int, int]) -> None:
    alpha, _ = seeded
    with transactional(sf) as s:
        doc_row_id = _document(s, alpha, "alpha-doc")

    _update(sf, alpha, doc_id=doc_row_id)

    with transactional(sf) as s:
        stored = s.execute(
            select(PlanSectionModel.doc_id).where(PlanSectionModel.letter == "A")
        ).scalar_one()
    assert stored == doc_row_id
    assert _counts(sf) == (3, 3)
