"""AFT-008 / RFC 27 F9: activity_service.summarize (GROUP BY) + actor_id filter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import activity_service as activity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SINCE = datetime(2026, 9, 7, tzinfo=UTC)
UNTIL = datetime(2026, 9, 20, 23, 59, 59, tzinfo=UTC)


def _seed_project(session: Session, slug: str = "ap") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug, root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _put(
    session: Session,
    proj_id: int,
    kind: str,
    *,
    actor_kind: str,
    actor_id: str,
    scope_kind: str,
    ts: datetime,
) -> None:
    ev = activity.emit(
        session,
        proj_id,
        kind,
        actor_kind=actor_kind,
        actor_id=actor_id,
        scope_kind=scope_kind,
    )
    ev.ts = ts


def _seed_events(session: Session, proj_id: int) -> None:
    # Внутри окна [SINCE, UNTIL]: 6 событий, 3 дня, 3 actor_kind,
    # 3 kind, 2 scope_kind, 3 actor_id.
    _put(
        session,
        proj_id,
        "task.created",
        actor_kind="human",
        actor_id="human:dakh",
        scope_kind="task",
        ts=datetime(2026, 9, 18, 10, tzinfo=UTC),
    )
    _put(
        session,
        proj_id,
        "doc.updated",
        actor_kind="human",
        actor_id="human:dakh",
        scope_kind="doc",
        ts=datetime(2026, 9, 18, 15, tzinfo=UTC),
    )
    _put(
        session,
        proj_id,
        "task.created",
        actor_kind="agent",
        actor_id="agent:claude-opus-5",
        scope_kind="task",
        ts=datetime(2026, 9, 19, 9, tzinfo=UTC),
    )
    _put(
        session,
        proj_id,
        "task.completed",
        actor_kind="agent",
        actor_id="agent:claude-opus-5",
        scope_kind="task",
        ts=datetime(2026, 9, 19, 12, tzinfo=UTC),
    )
    _put(
        session,
        proj_id,
        "doc.updated",
        actor_kind="routine",
        actor_id="routine:doc_drift_daily",
        scope_kind="doc",
        ts=datetime(2026, 9, 20, 8, tzinfo=UTC),
    )
    _put(
        session,
        proj_id,
        "task.created",
        actor_kind="agent",
        actor_id="agent:claude-opus-5",
        scope_kind="task",
        ts=datetime(2026, 9, 20, 9, tzinfo=UTC),
    )
    # Вне окна: до since и после until.
    _put(
        session,
        proj_id,
        "doc.updated",
        actor_kind="human",
        actor_id="human:dakh",
        scope_kind="doc",
        ts=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )
    _put(
        session,
        proj_id,
        "link.synced",
        actor_kind="routine",
        actor_id="routine:doc_drift_daily",
        scope_kind="link",
        ts=datetime(2026, 9, 25, 10, tzinfo=UTC),
    )


def test_summary_by_day_and_actor_kind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_events(session, proj_id)

    with transactional(factory) as session:
        rows = activity.summarize(
            session, proj_id, since=SINCE, until=UNTIL, group_by=["day", "actor_kind"]
        )
        assert rows == [
            {"day": "2026-09-18", "actor_kind": "human", "n": 2},
            {"day": "2026-09-19", "actor_kind": "agent", "n": 2},
            {"day": "2026-09-20", "actor_kind": "agent", "n": 1},
            {"day": "2026-09-20", "actor_kind": "routine", "n": 1},
        ]


def test_summary_single_keys(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_events(session, proj_id)

    with transactional(factory) as session:
        by_kind = activity.summarize(session, proj_id, since=SINCE, until=UNTIL, group_by=["kind"])
        assert by_kind == [
            {"kind": "doc.updated", "n": 2},
            {"kind": "task.completed", "n": 1},
            {"kind": "task.created", "n": 3},
        ]
        by_scope = activity.summarize(
            session, proj_id, since=SINCE, until=UNTIL, group_by=["scope_kind"]
        )
        assert by_scope == [
            {"scope_kind": "doc", "n": 2},
            {"scope_kind": "task", "n": 4},
        ]


def test_summary_until_bounds(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_events(session, proj_id)

    with transactional(factory) as session:
        rows = activity.summarize(
            session,
            proj_id,
            since=datetime(2026, 9, 18, tzinfo=UTC),
            until=datetime(2026, 9, 19, 23, 59, 59, tzinfo=UTC),
            group_by=["day"],
        )
        assert rows == [
            {"day": "2026-09-18", "n": 2},
            {"day": "2026-09-19", "n": 2},
        ]


def test_summary_total_matches_list_events(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_events(session, proj_id)

    with transactional(factory) as session:
        listed = activity.list_events(session, proj_id, since=SINCE, until=UNTIL, limit=1000)
        assert listed["total"] == 6
        for group_by in (["day"], ["actor_kind", "kind"]):
            rows = activity.summarize(session, proj_id, since=SINCE, until=UNTIL, group_by=group_by)
            assert sum(row["n"] for row in rows) == listed["total"]


def test_summary_invalid_group_by_lists_allowed(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_events(session, proj_id)

    with transactional(factory) as session:
        for bad in (["week"], [], ["day", "day"]):
            with pytest.raises(ValueError) as exc_info:
                activity.summarize(session, proj_id, since=SINCE, group_by=bad)
            message = str(exc_info.value)
            for key in ("day", "actor_kind", "kind", "scope_kind"):
                assert key in message


def test_list_events_actor_id_filter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        proj_id = _seed_project(session)
        _seed_events(session, proj_id)

    with transactional(factory) as session:
        result = activity.list_events(session, proj_id, actor_id="agent:claude-opus-5")
        assert result["total"] == 3
        assert {e["kind"] for e in result["items"]} == {"task.created", "task.completed"}
