"""Сверка партиции находок: закрыть вылеченное, вернуть рецидив.

До этого в `finding` не было ни того, ни другого: наружу торчали только
`dismiss` и `promote`, а `ingest_findings` на конфликте поднимал `times_seen`
и не трогал статус. Значит вылеченная и снова появившаяся находка не вернулась
бы в очередь никогда — `curator_next` смотрит только `status="open"`.

Тесты живут отдельно от потребителя (`doc_node_health`): механика
переиспользуемая, и ломаться она должна своим именем.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, FindingModel, ProjectModel
from cod_doc.services import finding_service
from cod_doc.services.finding_service import FindingSeed

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SOURCE = "routine"
_REF = "doc_node_health"
_OTHER_REF = "doc_node_health_ai"


@pytest.fixture
def session_factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _ingest(session: Session, project_id: int, fps: list[str], *, ref: str = _REF) -> None:
    finding_service.ingest_findings(
        session,
        project_id=project_id,
        source_run_id=f"run-{'-'.join(fps) or 'empty'}",
        seeds=[
            FindingSeed(
                fingerprint=fp,
                source=_SOURCE,
                source_ref=ref,
                title=f"gap {fp}",
                severity="minor",
            )
            for fp in fps
        ],
    )
    session.flush()


def _reconcile(session: Session, project_id: int, fps: set[str], *, ref: str = _REF) -> dict:
    return finding_service.reconcile_partition(
        session,
        project_id=project_id,
        source=_SOURCE,
        source_ref=ref,
        seen_fingerprints=fps,
        author="routine:doc_node_health",
    )


def _status(session: Session, fp: str) -> str:
    f = session.execute(select(FindingModel).where(FindingModel.fingerprint == fp)).scalar_one()
    return f.status


def test_healed_finding_is_resolved(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        result = _reconcile(session, pid, {"a"})

        assert result == {"resolved": 1, "reopened": 0}
        assert _status(session, "a") == "open"
        assert _status(session, "b") == "resolved"


def test_recurrence_reopens_a_resolved_finding(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Раздел наполнили, потом он снова просел — находка обязана вернуться.

    Без переоткрытия она осталась бы `resolved` навсегда: `ingest_findings`
    поднимает только `times_seen`, а очередь куратора фильтрует по `open`.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])
        _reconcile(session, pid, set())
        assert _status(session, "a") == "resolved"

        _ingest(session, pid, ["a"])
        result = _reconcile(session, pid, {"a"})

        assert result == {"resolved": 0, "reopened": 1}
        assert _status(session, "a") == "open"


def test_dismissed_survives_both_directions(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Решение человека автоматика не отменяет — ни закрытием, ни возвратом."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])
        uid = session.execute(
            select(FindingModel.finding_uid).where(FindingModel.fingerprint == "a")
        ).scalar_one()
        finding_service.dismiss_finding(
            session, project_id=pid, finding_uid=uid, author="human:test"
        )

        assert _reconcile(session, pid, set()) == {"resolved": 0, "reopened": 0}
        assert _status(session, "a") == "dismissed"

        assert _reconcile(session, pid, {"a"}) == {"resolved": 0, "reopened": 0}
        assert _status(session, "a") == "dismissed"


def test_other_partition_is_untouched(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Упавший LLM-проход не вправе закрывать детерминированные находки."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"], ref=_REF)
        _ingest(session, pid, ["b"], ref=_OTHER_REF)

        _reconcile(session, pid, set(), ref=_OTHER_REF)

        assert _status(session, "a") == "open", "чужая партиция не тронута"
        assert _status(session, "b") == "resolved"


def test_reconcile_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])

        first = _reconcile(session, pid, set())
        second = _reconcile(session, pid, set())

        assert first == {"resolved": 1, "reopened": 0}
        assert second == {"resolved": 0, "reopened": 0}, "второй прогон ничего не меняет"


def test_status_change_leaves_an_event(session_factory) -> None:  # type: ignore[no-untyped-def]
    """ADO-040: у находок нет ревизий, поэтому след — событие."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])
        _reconcile(session, pid, set())
        _ingest(session, pid, ["a"])
        _reconcile(session, pid, {"a"})
        session.flush()

        kinds = {e.kind for e in session.query(ActivityEventModel).all()}
        assert {"finding.resolved", "finding.reopened"} <= kinds

        actors = {
            e.actor_kind
            for e in session.query(ActivityEventModel).all()
            if e.kind.startswith("finding.")
        }
        assert actors == {"routine"}, "actor_kind выводится из автора, а не проставляется руками"
