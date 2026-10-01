"""ADO-228 (RFC 27 F9, N7): revision_summary — GROUP BY aggregate over revisions.

Revisions are seeded directly via RevisionModel with literal `at`/`author`/
`entity_kind` values, so expected results are literal lists of dicts — no
expectation is derived by calling `summarize` on another input or by
grouping the `list_for_project` feed in Python. The single exception is
`test_summary_total_matches_feed`, where comparing against the feed is the
subject of acceptance criterion 2 (and a literal count is asserted too).
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


def _seed_project(session, slug: str) -> int:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
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


def _seed_summary(session) -> int:  # type: ignore[no-untyped-def]
    """Main project feed: 5 revisions spanning 2026-09-20 … 2026-09-26.

    Plus one revision of a second project in the same DB for isolation
    checks. Returns the main project_id.
    """
    project_id = _seed_project(session, "p")
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
    _add_revision(
        session,
        project_id=project_id,
        entity_kind=EntityKind.TASK,
        entity_id=1,
        author="bob",
        at=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
        reason="task-26",
    )
    other_id = _seed_project(session, "q")
    _add_revision(
        session,
        project_id=other_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_id=9,
        author="carol",
        at=datetime(2026, 9, 26, 9, 0, tzinfo=UTC),
        reason="other-project",
    )
    return project_id


def test_summary_by_entity_kind_today(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_summary(session)
    with transactional(session_factory) as session:
        rows = revision_service.summarize(
            session,
            project_id,
            since=datetime(2026, 9, 26, 0, 0, tzinfo=UTC),
            group_by=["entity_kind"],
        )
    # ORDER BY entity_kind; ревизия второго проекта (document, carol) не попадает.
    assert rows == [
        {"entity_kind": "document", "n": 1},
        {"entity_kind": "task", "n": 1},
    ]


def test_summary_by_day_and_author(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_summary(session)
    with transactional(session_factory) as session:
        rows = revision_service.summarize(
            session,
            project_id,
            since=datetime(2026, 9, 20, 0, 0, tzinfo=UTC),
            group_by=["day", "author"],
        )
    assert rows == [
        {"day": "2026-09-20", "author": "alice", "n": 1},
        {"day": "2026-09-25", "author": "alice", "n": 1},
        {"day": "2026-09-25", "author": "bob", "n": 1},
        {"day": "2026-09-26", "author": "alice", "n": 1},
        {"day": "2026-09-26", "author": "bob", "n": 1},
    ]


def test_summary_until_bounds(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_summary(session)
    with transactional(session_factory) as session:
        rows = revision_service.summarize(
            session,
            project_id,
            since=datetime(2026, 9, 25, 0, 0, tzinfo=UTC),
            until=datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC),
            group_by=["day"],
        )
    assert rows == [{"day": "2026-09-25", "n": 2}]


def test_summary_total_matches_feed(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_summary(session)
    with transactional(session_factory) as session:
        since = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
        until = datetime(2026, 9, 26, 23, 59, 59, tzinfo=UTC)
        feed_len = len(
            revision_service.list_for_project(
                session, project_id, since=since, until=until, limit=1000
            )
        )
        for group_by in (["entity_kind"], ["day", "author"]):
            rows = revision_service.summarize(
                session, project_id, since=since, until=until, group_by=group_by
            )
            assert sum(row["n"] for row in rows) == feed_len
    assert feed_len == 5


def test_summary_invalid_group_by_lists_allowed(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_summary(session)
    with transactional(session_factory) as session:
        for group_by in (["week"], [], ["day", "day"]):
            with pytest.raises(ValueError) as excinfo:
                revision_service.summarize(
                    session,
                    project_id,
                    since=datetime(2026, 9, 20, 0, 0, tzinfo=UTC),
                    group_by=group_by,
                )
            message = str(excinfo.value)
            assert "day" in message
            assert "entity_kind" in message
            assert "author" in message


def test_list_for_project_until(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        project_id = _seed_summary(session)
    with transactional(session_factory) as session:
        feed = revision_service.list_for_project(
            session,
            project_id,
            until=datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC),
        )
    assert [r.reason for r in feed] == ["section-25", "task-25", "old-task"]
