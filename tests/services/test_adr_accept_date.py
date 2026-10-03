"""ARG-002 (RFC 34 F1, F2): принятие ставит дату решения; дату можно очистить."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel, RevisionModel
from cod_doc.services import adr_service

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def _seed(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="adrs", title="P", root_path="/tmp", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _last_diff(session: Session, row_id: int) -> dict[str, object]:
    diff = session.execute(
        select(RevisionModel.diff)
        .where(RevisionModel.entity_kind == EntityKind.ADR, RevisionModel.entity_id == row_id)
        .order_by(RevisionModel.row_id.desc())
        .limit(1)
    ).scalar_one()
    return json.loads(diff)


def test_accept_without_date_stamps_today(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    today = datetime.now(UTC).date()
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X", adr_id="ADR-015")
        row = adr_service.update(session, project_id=pid, adr_id="ADR-015", status="accepted")
        assert row.decided_at == today
        diff = _last_diff(session, row.row_id)
    assert diff["status"] == {"old": "proposed", "new": "accepted"}
    assert diff["decided_at"] == {"old": None, "new": today.isoformat()}


def test_accept_with_explicit_date_keeps_it(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X", adr_id="ADR-015")
        row = adr_service.update(
            session,
            project_id=pid,
            adr_id="ADR-015",
            status="accepted",
            decided_at=date(2026, 9, 18),
        )
    assert row.decided_at == date(2026, 9, 18)


def test_accept_keeps_date_already_on_the_row(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(
            session, project_id=pid, title="X", adr_id="ADR-009", decided_at=date(2026, 5, 17)
        )
        row = adr_service.update(session, project_id=pid, adr_id="ADR-009", status="accepted")
    assert row.decided_at == date(2026, 5, 17)


def test_non_accept_status_change_leaves_date_empty(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X", adr_id="ADR-001")
        row = adr_service.update(session, project_id=pid, adr_id="ADR-001", status="rejected")
    assert row.decided_at is None


def test_sync_body_clears_decided_at(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(
            session, project_id=pid, title="X", adr_id="ADR-009", decided_at=date(2026, 5, 17)
        )
        row = adr_service.sync_body(
            session, project_id=pid, adr_id="ADR-009", clear_decided_at=True
        )
        assert row.decided_at is None
        diff = _last_diff(session, row.row_id)
    assert diff["op"] == "sync_body"
    assert diff["decided_at"] == {"old": "2026-05-17", "new": None}


def test_sync_body_clear_on_empty_date_is_noop(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        created = adr_service.create(session, project_id=pid, title="X", adr_id="ADR-009")
        before = _last_diff(session, created.row_id)
        adr_service.sync_body(session, project_id=pid, adr_id="ADR-009", clear_decided_at=True)
        assert _last_diff(session, created.row_id) == before


def test_sync_body_clear_and_date_together_is_an_error(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_service.create(session, project_id=pid, title="X", adr_id="ADR-009")
        with pytest.raises(ValueError, match="mutually exclusive"):
            adr_service.sync_body(
                session,
                project_id=pid,
                adr_id="ADR-009",
                decided_at=date(2026, 1, 1),
                clear_decided_at=True,
            )
