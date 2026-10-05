"""ARG-008 (RFC 34 §3.4): полки реестра ADR и привязка решения к полке."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ADRModel, ProjectModel, RevisionModel
from cod_doc.services import adr_service, adr_topic_service
from cod_doc.services.adr_service import ADRImmutableError
from cod_doc.services.adr_topic_service import ADRTopicExistsError, ADRTopicNotFoundError

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
    pid = proj.row_id
    adr_service.create(
        session, project_id=pid, title="PostgreSQL", adr_id="ADR-005", status="accepted"
    )
    adr_service.create(session, project_id=pid, title="Реплики", adr_id="ADR-016")
    return pid


def _names(session: Session, pid: int) -> list[str]:
    return [t.name for t in adr_topic_service.list_for_project(session, pid)]


def _adr_ops(session: Session, adr_id: str) -> list[dict[str, object]]:
    row_id = session.execute(select(ADRModel.row_id).where(ADRModel.adr_id == adr_id)).scalar_one()
    return [
        json.loads(d)
        for d in session.execute(
            select(RevisionModel.diff)
            .where(RevisionModel.entity_kind == EntityKind.ADR, RevisionModel.entity_id == row_id)
            .order_by(RevisionModel.row_id)
        ).scalars()
    ]


def test_create_appends_and_rejects_duplicates(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        a = adr_topic_service.create(
            session, project_id=pid, name="  Хранение  ", includes="SQLite, PostgreSQL"
        )
        b = adr_topic_service.create(session, project_id=pid, name="Агент")
        assert (a.name, a.position, b.position) == ("Хранение", 0, 1)
        assert a.includes == "SQLite, PostgreSQL"
        with pytest.raises(ADRTopicExistsError):
            adr_topic_service.create(session, project_id=pid, name="Хранение")
        with pytest.raises(ValueError, match="empty"):
            adr_topic_service.create(session, project_id=pid, name="   ")
        revs = (
            session.execute(
                select(RevisionModel.diff).where(RevisionModel.entity_kind == EntityKind.ADR_TOPIC)
            )
            .scalars()
            .all()
        )
        assert [json.loads(d)["op"] for d in revs] == ["create", "create"]


def test_update_renames_and_edits(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_topic_service.create(session, project_id=pid, name="Хранение")
        adr_topic_service.create(session, project_id=pid, name="Агент")
        t = adr_topic_service.update(
            session, project_id=pid, name="Хранение", new_name="Данные", excludes="Эмбеддинги"
        )
        assert (t.name, t.excludes) == ("Данные", "Эмбеддинги")
        with pytest.raises(ADRTopicExistsError):
            adr_topic_service.update(session, project_id=pid, name="Данные", new_name="Агент")
        with pytest.raises(ADRTopicNotFoundError):
            adr_topic_service.update(session, project_id=pid, name="Нет такой", includes="x")


def test_move_renumbers_without_gaps(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        for name in ("A", "B", "C", "D"):
            adr_topic_service.create(session, project_id=pid, name=name)
        adr_topic_service.move(session, project_id=pid, name="D", position=1)
        assert _names(session, pid) == ["A", "D", "B", "C"]
        # За концом списка — зажимается в последнюю.
        adr_topic_service.move(session, project_id=pid, name="A", position=99)
        assert _names(session, pid) == ["D", "B", "C", "A"]
        positions = [t.position for t in adr_topic_service.list_for_project(session, pid)]
        assert positions == [0, 1, 2, 3]


def test_set_topic_on_accepted_and_back(engine_with_schema: Engine) -> None:
    """Полку можно сменить у accepted: это место решения, а не его текст."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_topic_service.create(session, project_id=pid, name="Хранение")
        row = adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="Хранение")
        assert adr_service.topic_name(session, row) == "Хранение"
        assert adr_service.adr_to_dict(session, row)["topic"] == "Хранение"
        # Тело по-прежнему заморожено.
        with pytest.raises(ADRImmutableError):
            adr_service.update(session, project_id=pid, adr_id="ADR-005", title="X")
        # Повтор — no-op, без новой ревизии.
        before = len(_adr_ops(session, "ADR-005"))
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="Хранение")
        assert len(_adr_ops(session, "ADR-005")) == before
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic=None)
        ops = [d for d in _adr_ops(session, "ADR-005") if d["op"] == "set_topic"]
    assert [(d["old"], d["new"]) for d in ops] == [(None, "Хранение"), ("Хранение", None)]


def test_set_topic_requires_existing_topic(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        with pytest.raises(ADRTopicNotFoundError):
            adr_service.set_topic(session, project_id=pid, adr_id="ADR-016", topic="Нет такой")


def test_delete_unshelves_adrs_with_revisions(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        for name in ("Хранение", "Агент", "Команда"):
            adr_topic_service.create(session, project_id=pid, name=name)
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="Агент")
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-016", topic="Агент")
        moved = adr_topic_service.delete(session, project_id=pid, name="Агент")
        assert moved == 2
        assert _names(session, pid) == ["Хранение", "Команда"]
        assert [t.position for t in adr_topic_service.list_for_project(session, pid)] == [0, 1]
        rows = session.execute(select(ADRModel.adr_id, ADRModel.topic_id)).all()
        assert {r.adr_id: r.topic_id for r in rows} == {"ADR-005": None, "ADR-016": None}
        last = _adr_ops(session, "ADR-016")[-1]
    assert (last["op"], last["old"], last["new"]) == ("set_topic", "Агент", None)


def test_adr_counts(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        t = adr_topic_service.create(session, project_id=pid, name="Хранение")
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="Хранение")
        assert adr_topic_service.adr_counts(session, pid) == {t.row_id: 1}
