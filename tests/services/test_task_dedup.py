"""COD-066: title-based deduplication for task_service.create."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel
from cod_doc.services import task_service
from cod_doc.services.task_service import (
    DuplicateTaskError,
    _normalize_title,
    find_duplicate_by_title,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed(session: Session) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="p-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    sec = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
    )
    session.add(sec)
    session.flush()
    return proj.row_id, plan.row_id, sec.row_id


def _create(session, p, pl, s, tid: str, title: str, **kwargs) -> object:  # type: ignore[no-untyped-def]
    return task_service.create(
        session,
        project_id=p,
        plan_id=pl,
        section_id=s,
        task_id=tid,
        title=title,
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# _normalize_title — pure helper
# ---------------------------------------------------------------------------


def test_normalize_title_collapses_case_and_punctuation() -> None:
    assert _normalize_title("Implement: Auth-Service!") == "implement auth service"
    assert _normalize_title("  Implement   auth   service  ") == "implement auth service"
    assert _normalize_title("Implement: Auth/Service") == _normalize_title(
        "implement auth service"
    )


def test_normalize_title_unicode_keeps_word_chars() -> None:
    assert _normalize_title("Реализовать: Авто-логин!") == "реализовать авто логин"


def test_normalize_title_empty_string() -> None:
    assert _normalize_title("") == ""
    assert _normalize_title("   ") == ""
    assert _normalize_title("!!!") == ""


# ---------------------------------------------------------------------------
# find_duplicate_by_title
# ---------------------------------------------------------------------------


def test_find_duplicate_returns_match(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _create(session, p, pl, s, "PR-001", "Implement: search by tag")

        # Different case + punctuation, same meaning
        match = find_duplicate_by_title(session, p, "implement search by tag!!")
        assert match is not None
        assert match.task_id == "PR-001"


def test_find_duplicate_returns_none_when_no_match(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _create(session, p, pl, s, "PR-001", "Implement: search by tag")

        assert find_duplicate_by_title(session, p, "Refactor: index loader") is None


def test_find_duplicate_scoped_to_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A same-title task in a *different* project must not be reported."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p1, pl1, s1 = _seed(session)
        # Second project
        now = datetime.now(UTC)
        p2_model = ProjectModel(slug="p2", title="P2", root_path="/tmp/p2", config_json={})
        p2_model.created = now
        p2_model.updated = now
        session.add(p2_model)
        session.flush()
        plan2 = PlanModel(
            project_id=p2_model.row_id, scope="p2-plan", created=now, last_updated=now
        )
        session.add(plan2)
        session.flush()
        sec2 = PlanSectionModel(
            plan_id=plan2.row_id, letter="A", title="Core", slug="A-Core", position=0
        )
        session.add(sec2)
        session.flush()

        _create(session, p1, pl1, s1, "PR-001", "Implement: shared title")
        _create(session, p2_model.row_id, plan2.row_id, sec2.row_id, "PRX-001", "Implement: shared title")

        # Probe project p2: should NOT find the task in p1
        match = find_duplicate_by_title(session, p2_model.row_id, "Implement: shared title")
        assert match is not None
        assert match.task_id == "PRX-001"


# ---------------------------------------------------------------------------
# create() with allow_duplicate flag
# ---------------------------------------------------------------------------


def test_create_with_allow_duplicate_false_raises_on_match(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _create(session, p, pl, s, "PR-001", "Implement: search by tag")

        with pytest.raises(DuplicateTaskError) as exc:
            _create(
                session,
                p,
                pl,
                s,
                "PR-002",
                "implement search by tag",  # different case → matches
                allow_duplicate=False,
            )
        assert exc.value.existing_task_id == "PR-001"
        assert exc.value.normalized_title == "implement search by tag"


def test_create_with_allow_duplicate_true_inserts(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Default `allow_duplicate=True` preserves legacy behaviour."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _create(session, p, pl, s, "PR-001", "Implement: search by tag")

        # explicit override allowed
        _create(
            session,
            p,
            pl,
            s,
            "PR-002",
            "Implement: search by tag",
            allow_duplicate=True,
        )

        # Both tasks now present
        rows = task_service.list_for_project(session, p)
        assert {r.task_id for r in rows} == {"PR-001", "PR-002"}


def test_create_default_allows_duplicate_for_legacy_callers(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Default behaviour is permissive — legacy callers and tests stay green."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p, pl, s = _seed(session)
        _create(session, p, pl, s, "PR-001", "Implement: search by tag")
        # No allow_duplicate kwarg → default True
        _create(session, p, pl, s, "PR-002", "Implement: search by tag")

        rows = task_service.list_for_project(session, p)
        assert len(rows) == 2
