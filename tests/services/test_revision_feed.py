"""AFT-008 (RFC 27 F9): project revision feed filters and list_for_entity limit.

Revisions are seeded directly via RevisionModel with literal `at`/`author`/
`entity_kind` values, so expected results are literal lists of reasons — no
expectation is derived by calling the service under test and filtering in
Python.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ulid import ULID

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel, RevisionModel
from cod_doc.services import revision_service


@pytest.fixture
def session_factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _seed_project(session) -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _add_revision(  # type: ignore[no-untyped-def]
    session,
    *,
    project_id: int,
    entity_kind: EntityKind,
    entity_id: int,
    author: str,
    at: datetime,
    reason: str,
) -> None:
    session.add(
        RevisionModel(
            revision_id=str(ULID.from_datetime(at)),
            project_id=project_id,
            entity_kind=entity_kind.value,
            entity_id=entity_id,
            author=author,
            at=at,
            diff="{}",
            reason=reason,
        )
    )


def _seed_feed(session) -> int:  # type: ignore[no-untyped-def]
    """Project feed: 4 revisions spanning 2026-09-20 … 2026-09-26."""
    project_id = _seed_project(session)
    _add_revision(
        session,
        project_id=project_id,
        entity_kind=EntityKind.TASK,
        entity_id=1,
        author="alice",
        at=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
        reason="old-task",
    )
    _add_revision(
        session,
        project_id=project_id,
        entity_kind=EntityKind.TASK,
        entity_id=1,
        author="alice",
        at=datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
        reason="task-25",
    )
    _add_revision(
        session,
        project_id=project_id,
        entity_kind=EntityKind.SECTION,
        entity_id=2,
        author="bob",
        at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
        reason="section-25",
    )
    _add_revision(
        session,
        project_id=project_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_id=3,
        author="alice",
        at=datetime(2026, 9, 26, 8, 0, tzinfo=UTC),
        reason="doc-26",
    )
    return project_id


def test_feed_since_filters(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_feed(session)
    with transactional(session_factory) as session:
        feed = revision_service.list_for_project(
            session,
            project_id,
            since=datetime(2026, 9, 25, 0, 0, tzinfo=UTC),
        )
    assert [r.reason for r in feed] == ["doc-26", "section-25", "task-25"]


def test_feed_author_filter(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_feed(session)
    with transactional(session_factory) as session:
        feed = revision_service.list_for_project(session, project_id, author="alice")
    assert [r.reason for r in feed] == ["doc-26", "task-25", "old-task"]


def test_feed_entity_kind_and_since_combined(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_feed(session)
    with transactional(session_factory) as session:
        feed = revision_service.list_for_project(
            session,
            project_id,
            entity_kind=EntityKind.TASK,
            since=datetime(2026, 9, 25, 0, 0, tzinfo=UTC),
        )
    assert [r.reason for r in feed] == ["task-25"]


def _seed_entity_history(session) -> None:  # type: ignore[no-untyped-def]
    """One task with 5 revisions on consecutive days, 2026-09-20 … 2026-09-24."""
    project_id = _seed_project(session)
    for day, reason in enumerate(["one", "two", "three", "four", "five"], start=20):
        _add_revision(
            session,
            project_id=project_id,
            entity_kind=EntityKind.TASK,
            entity_id=7,
            author="alice",
            at=datetime(2026, 9, day, 10, 0, tzinfo=UTC),
            reason=reason,
        )


def test_list_for_entity_limit_returns_tail_oldest_first(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        _seed_entity_history(session)
    with transactional(session_factory) as session:
        tail = revision_service.list_for_entity(session, EntityKind.TASK, 7, limit=2)
    assert [r.reason for r in tail] == ["four", "five"]


def test_list_for_entity_without_limit_unchanged(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        _seed_entity_history(session)
    with transactional(session_factory) as session:
        history = revision_service.list_for_entity(session, EntityKind.TASK, 7)
    assert [r.reason for r in history] == ["one", "two", "three", "four", "five"]
