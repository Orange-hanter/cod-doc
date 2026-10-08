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
from cod_doc.services.adr_service import ADRImmutableError, ADRNotFoundError
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
                select(RevisionModel.diff)
                .where(RevisionModel.entity_kind == EntityKind.ADR_TOPIC)
                .order_by(RevisionModel.row_id)
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


def _second_project(session: Session) -> int:
    now = datetime.now(UTC)
    other = ProjectModel(slug="other", title="O", root_path="/tmp/o", config_json={})
    other.created = now
    other.updated = now
    session.add(other)
    session.flush()
    return other.row_id


def test_topics_are_project_scoped(engine_with_schema: Engine) -> None:
    """Полки и решения одного проекта не видны и не трогаются из другого."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        other = _second_project(session)
        adr_topic_service.create(session, project_id=pid, name="Хранение")
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="Хранение")
        with pytest.raises(ADRNotFoundError):
            adr_service.set_topic(session, project_id=other, adr_id="ADR-005", topic=None)
        with pytest.raises(ADRTopicNotFoundError):
            adr_topic_service.delete(session, project_id=other, name="Хранение")
        with pytest.raises(ADRTopicNotFoundError):
            adr_topic_service.update(session, project_id=other, name="Хранение", includes="x")
        # Одноимённая полка в другом проекте — отдельная строка, не дубликат.
        adr_topic_service.create(session, project_id=other, name="Хранение")
        assert adr_topic_service.names_by_id(session, other) != adr_topic_service.names_by_id(
            session, pid
        )
        assert adr_topic_service.adr_counts(session, other) == {}
        assert adr_service.topic_name(session, _adr(session, "ADR-005")) == "Хранение"


def _adr(session: Session, adr_id: str) -> ADRModel:
    return session.execute(select(ADRModel).where(ADRModel.adr_id == adr_id)).scalar_one()


def test_scope_text_is_bounded(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        too_long = "x" * (adr_topic_service.SCOPE_MAX + 1)
        with pytest.raises(ValueError, match="includes is longer"):
            adr_topic_service.create(session, project_id=pid, name="A", includes=too_long)
        adr_topic_service.create(session, project_id=pid, name="A")
        with pytest.raises(ValueError, match="excludes is longer"):
            adr_topic_service.update(session, project_id=pid, name="A", excludes=too_long)


def test_deleted_topic_row_id_is_not_reused(engine_with_schema: Engine) -> None:
    """Висячий ``topic_id`` (очистку обошли прямым SQL) не указывает на новую полку."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        gone = adr_topic_service.create(session, project_id=pid, name="Старая").row_id
        session.delete(adr_topic_service.require(session, pid, "Старая"))
        session.flush()
        fresh = adr_topic_service.create(session, project_id=pid, name="Новая").row_id
    assert fresh != gone


def test_lookup_normalises_name(engine_with_schema: Engine) -> None:
    """Имя ищется в том же виде, в каком записано: пробелы по краям и внутри схлопнуты."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_topic_service.create(session, project_id=pid, name="Хранение  данных")
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic=" Хранение данных ")
        adr_topic_service.move(session, project_id=pid, name="Хранение   данных", position=0)
        assert adr_topic_service.require(session, pid, "Хранение данных ").name == (
            "Хранение данных"
        )


def test_loose_count_and_names(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        other = _second_project(session)
        adr_service.create(session, project_id=other, title="Чужое", adr_id="ADR-001")
        adr_topic_service.create(session, project_id=pid, name="Б")
        adr_topic_service.create(session, project_id=pid, name="А")
        adr_topic_service.move(session, project_id=pid, name="А", position=0)
        assert adr_topic_service.names(session, pid) == ["А", "Б"]
        assert adr_topic_service.loose_count(session, pid) == 2
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="А")
        assert adr_topic_service.loose_count(session, pid) == 1


def test_topic_name_ignores_other_projects_topic(engine_with_schema: Engine) -> None:
    """Испорченный topic_id, указывающий на полку другого проекта, не раскрывает её имя."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed(session)
        other = _second_project(session)
        foreign = adr_topic_service.create(session, project_id=other, name="Чужая")
        row = _adr(session, "ADR-005")
        row.topic_id = foreign.row_id
        session.flush()
        assert adr_service.topic_name(session, row) is None


def _topic_ops(session: Session) -> list[dict[str, object]]:
    return [
        json.loads(d)
        for d in session.execute(
            select(RevisionModel.diff)
            .where(RevisionModel.entity_kind == EntityKind.ADR_TOPIC)
            .order_by(RevisionModel.row_id)
        ).scalars()
    ]


def test_move_and_delete_record_shifted_neighbours(engine_with_schema: Engine) -> None:
    """Перенумерация соседей — тоже запись: сдвиг виден в ревизии move и delete."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        for name in ("А", "Б", "В"):
            adr_topic_service.create(session, project_id=pid, name=name)
        adr_topic_service.move(session, project_id=pid, name="В", position=0)
        adr_topic_service.delete(session, project_id=pid, name="В")
        ops = _topic_ops(session)
    move, delete = ops[-2], ops[-1]
    assert (move["op"], move["old"], move["new"]) == ("move", 2, 0)
    assert move["shifted"] == {"А": [0, 1], "Б": [1, 2]}
    assert delete["op"] == "delete"
    assert delete["shifted"] == {"А": [1, 0], "Б": [2, 1]}


def test_update_records_old_and_new_scope(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_topic_service.create(session, project_id=pid, name="А", includes="SQLite")
        adr_topic_service.update(
            session, project_id=pid, name="А", includes="PostgreSQL", excludes="Векторы"
        )
        last = _topic_ops(session)[-1]
    assert last["includes"] == {"old": "SQLite", "new": "PostgreSQL"}
    assert last["excludes"] == {"old": "", "new": "Векторы"}


def test_move_rejects_negative_position(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_topic_service.create(session, project_id=pid, name="А")
        with pytest.raises(ValueError, match="position must be >= 0"):
            adr_topic_service.move(session, project_id=pid, name="А", position=-1)


@pytest.mark.parametrize(
    ("op", "field"),
    [
        ("create", "includes"),
        ("create", "excludes"),
        ("update", "includes"),
        ("update", "excludes"),
    ],
)
def test_scope_limit_is_inclusive(engine_with_schema: Engine, op: str, field: str) -> None:
    """Ровно SCOPE_MAX проходит, на символ больше — нет; на обоих полях и в обеих операциях."""
    factory = make_session_factory(engine_with_schema)
    limit = adr_topic_service.SCOPE_MAX
    with transactional(factory) as session:
        pid = _seed(session)
        if op == "update":
            adr_topic_service.create(session, project_id=pid, name="А")

        def write(value: str) -> None:
            if op == "create":
                adr_topic_service.create(
                    session, project_id=pid, name=f"А{len(value)}", **{field: value}
                )
            else:
                adr_topic_service.update(session, project_id=pid, name="А", **{field: value})

        write("x" * limit)
        stored = adr_topic_service.require(session, pid, "А" if op == "update" else f"А{limit}")
        assert len(getattr(stored, field)) == limit
        with pytest.raises(ValueError, match=f"{field} is longer than {limit}"):
            write("y" * (limit + 1))


def test_dangling_topic_id_resolves_to_no_topic(engine_with_schema: Engine) -> None:
    """Полку удалили мимо сервиса: ADR остаётся с висячим topic_id, новая полка его не подхватывает."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        adr_topic_service.create(session, project_id=pid, name="Старая")
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="Старая")
        session.delete(adr_topic_service.require(session, pid, "Старая"))
        session.flush()
        adr_topic_service.create(session, project_id=pid, name="Новая")
        row = _adr(session, "ADR-005")
        assert row.topic_id is not None
        assert adr_service.topic_name(session, row) is None
        assert adr_topic_service.adr_counts(session, pid) == {row.topic_id: 1}
        # Снять висячую полку можно: старое имя в ревизии — None, а не чужая полка.
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic=None)
        last = _adr_ops(session, "ADR-005")[-1]
    assert (last["op"], last["old"], last["new"]) == ("set_topic", None, None)


def test_set_topic_does_not_leak_foreign_old_name(engine_with_schema: Engine) -> None:
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed(session)
        other = _second_project(session)
        foreign = adr_topic_service.create(session, project_id=other, name="Чужая")
        adr_topic_service.create(session, project_id=pid, name="Своя")
        row = _adr(session, "ADR-005")
        row.topic_id = foreign.row_id
        session.flush()
        adr_service.set_topic(session, project_id=pid, adr_id="ADR-005", topic="Своя")
        last = _adr_ops(session, "ADR-005")[-1]
    assert (last["old"], last["new"]) == (None, "Своя")


def test_topic_name_length_is_bounded_on_server(engine_with_schema: Engine) -> None:
    """Предел имени держит сервис, а не maxlength формы: и create, и rename."""
    factory = make_session_factory(engine_with_schema)
    limit = adr_topic_service.NAME_MAX
    with transactional(factory) as session:
        pid = _seed(session)
        adr_topic_service.create(session, project_id=pid, name="я" * limit)
        with pytest.raises(ValueError, match=f"longer than {limit}"):
            adr_topic_service.create(session, project_id=pid, name="ю" * (limit + 1))
        with pytest.raises(ValueError, match=f"longer than {limit}"):
            adr_topic_service.update(
                session, project_id=pid, name="я" * limit, new_name="ю" * (limit + 1)
            )
