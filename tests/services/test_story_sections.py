"""ADO-143: реестр секций историй + привязка истории к секции.

ADO-162, независимость от порядка. Плагина `pytest-reverse` в проекте нет,
поэтому обратный порядок проверяется сбором node id и прогоном их пачкой:

    pytest tests/services/test_story_sections.py \
           tests/cli/test_story_sections_cli.py \
           tests/api/test_web_stories_sections.py \
           tests/infra/test_story_section_migration.py \
           --collect-only -q | grep '::' | tail -r | xargs pytest -q

На 2026-09-18 — 55 кейсов, зелено в обе стороны. Держится это не удачей, а
autouse-фикстурой `isolated_cod_doc_home`: каждый кейс получает свой
`COD_DOC_HOME` и свою БД в `tmp_path`, так что состояния между кейсами нет.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import EntityKind, Priority, UserStoryStatus
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import revision_service as rev
from cod_doc.services import story_service as stories
from cod_doc.services.validation import ValidationError
from cod_doc.services.validation.structural import MAX_STORY_SECTION_TITLE

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return int(proj.row_id)


def _make_story(session: Session, project_id: int, story_id: str = "US-001", **kw):  # type: ignore[no-untyped-def]
    return stories.create(
        session,
        project_id=project_id,
        story_id=story_id,
        persona="Управляющий",
        narrative="Как управляющий, я хочу X, чтобы Y.",
        priority=Priority.MEDIUM,
        status=UserStoryStatus.ACCEPTED,
        author="human:test",
        **kw,
    )


# ============================================================================ #
# create_section                                                               #
# ============================================================================ #


def test_create_section_assigns_incremental_position(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        a = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        b = stories.create_section(
            session, project_id=pid, key="annex", title="Аннекс", author="human:test"
        )
        assert (a.position, b.position) == (1, 2)
        assert [s.key for s in stories.list_sections(session, pid)] == ["module-1", "annex"]


def test_create_section_rejects_unsafe_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ключ уходит в путь роута и в CSS-селектор — слэши, точки и пробелы под запретом."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        for bad in ("Module 1", "module/1", "module.1", "Модуль-1", "MODULE-1", ""):
            # Контракт — это код ошибки, а не проза сообщения: по нему
            # поверхности маршрутизируют отказ. В текст сообщения код не
            # входит, поэтому `match=` тут не годится — проверяем атрибут.
            with pytest.raises(ValidationError) as exc:
                stories.create_section(
                    session, project_id=pid, key=bad, title="X", author="human:test"
                )
            assert exc.value.code == "US-002"


def test_section_revision_does_not_land_in_story_history(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Адрес ревизии — пара (entity_kind, entity_id).

    У `story_section` и `user_story` независимая нумерация row_id, поэтому
    первая секция и первая история обе получают row_id=1. Пока секция писала
    ревизию под `EntityKind.STORY`, её `create_section` оказывался в истории
    чужой истории.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        sec = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        story = _make_story(session, pid)
        # Предпосылка теста: без совпадения row_id коллизию не поймать.
        assert sec.row_id == story.row_id
        story_row = story.row_id
        section_row = sec.row_id

    with transactional(factory) as session:
        story_hist = rev.list_for_entity(session, EntityKind.STORY, story_row)
        ops = [json.loads(r.diff)["op"] for r in story_hist]
        assert "create_section" not in ops, "ревизия секции просочилась в историю истории"

        section_hist = rev.list_for_entity(session, EntityKind.STORY_SECTION, section_row)
        assert [json.loads(r.diff)["op"] for r in section_hist] == ["create_section"]


@pytest.mark.parametrize("bad", ["", "   ", "\t\n"], ids=["empty", "spaces", "whitespace"])
def test_create_section_rejects_blank_title(engine_with_schema, bad: str) -> None:  # type: ignore[no-untyped-def]
    """ADO-160: пустой title рисует безымянную группу, неотличимую от «No section»."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        with pytest.raises(ValidationError) as exc:
            stories.create_section(
                session, project_id=pid, key="module-1", title=bad, author="human:test"
            )
        # Контракт — код, а не проза: по нему поверхности маршрутизируют отказ.
        assert exc.value.code == "US-003"


def test_create_section_rejects_oversized_title(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Потолок — вёрстка, а не безопасность: title подписывает группу и чип фильтра."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        with pytest.raises(ValidationError) as exc:
            stories.create_section(
                session,
                project_id=pid,
                key="module-1",
                title="x" * (MAX_STORY_SECTION_TITLE + 1),
                author="human:test",
            )
        assert exc.value.code == "US-003"
        # Граница включительно — ровно 256 проходит.
        stories.create_section(
            session,
            project_id=pid,
            key="module-2",
            title="x" * MAX_STORY_SECTION_TITLE,
            author="human:test",
        )


@pytest.mark.parametrize(
    ("bad", "label"),
    [
        ("Запасы\nи закупки", "newline"),
        ("Запасы\tи закупки", "tab"),
        ("Запасы\r\nи закупки", "crlf"),
        ("Запасы\x00", "nul"),
        ("Запасы\x1b[31m", "escape"),
    ],
    ids=lambda v: v if isinstance(v, str) and len(v) < 12 else "",
)
def test_create_section_rejects_control_characters(
    engine_with_schema, bad: str, label: str
) -> None:  # type: ignore[no-untyped-def]
    """Перевод строки посреди названия разъезжает подпись группы и чип фильтра.

    Экранирование делает Jinja, так что речь не про XSS, а про инвариант
    данных: в БД такое попадает молча и вылезает только на вёрстке.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        with pytest.raises(ValidationError) as exc:
            stories.create_section(
                session, project_id=pid, key="module-1", title=bad, author="human:test"
            )
        assert exc.value.code == "US-003", label


def test_create_section_keeps_internal_spaces(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Обычный пробел — не управляющий символ: многословные названия законны."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        sec = stories.create_section(
            session,
            project_id=pid,
            key="module-1",
            title="Управление запасами и закупки",
            author="human:test",
        )
        assert sec.title == "Управление запасами и закупки"


def test_create_section_rejects_negative_position(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        with pytest.raises(ValidationError) as exc:
            stories.create_section(
                session,
                project_id=pid,
                key="module-1",
                title="Запасы",
                position=-1,
                author="human:test",
            )
        assert exc.value.code == "US-004"


def test_create_section_accepts_position_zero(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ноль допустим СОЗНАТЕЛЬНО — это и есть зафиксированное правило.

    ``next_position`` раздаёт номера с единицы, поэтому 0 не может быть
    выдан автоматически: он остаётся ручным способом закрепить секцию выше
    всех прочих. Тест держит это решение, чтобы «заодно» его не ужесточили
    до ``>= 1``.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        pinned = stories.create_section(
            session,
            project_id=pid,
            key="pinned",
            title="Наверху",
            position=0,
            author="human:test",
        )
        auto = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        assert pinned.position == 0
        assert auto.position >= 1
        assert [s.key for s in stories.list_sections(session, pid)] == ["pinned", "module-1"]


def test_next_position_ignores_other_projects(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-161: агрегат обязан быть отфильтрован по проекту, как и выгрузка до него."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        other = ProjectModel(slug="q", title="Q", root_path="/tmp/q", config_json={})
        other.created = other.updated = datetime.now(UTC)
        session.add(other)
        session.flush()
        for i in range(5):
            stories.create_section(
                session,
                project_id=int(other.row_id),
                key=f"other-{i}",
                title=f"O{i}",
                author="human:test",
            )
        first = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        assert first.position == 1, "позиция посчитана по чужому проекту"


def test_create_section_rejects_duplicate_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        with pytest.raises(stories.SectionAlreadyExistsError, match="module-1"):
            stories.create_section(
                session, project_id=pid, key="module-1", title="Другое", author="human:test"
            )


# ============================================================================ #
# assign_section                                                               #
# ============================================================================ #


def test_assign_section_writes_revision_and_event(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        story = _make_story(session, pid)
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        row_id = story.row_id

    with transactional(factory) as session:
        assert stories.get(session, "US-001").section_id is not None
        history = rev.list_for_entity(session, EntityKind.STORY, row_id)
        ops = [json.loads(r.diff)["op"] for r in history]
        assert "section" in ops
        latest = next(json.loads(r.diff) for r in history if json.loads(r.diff)["op"] == "section")
        assert latest["old"] is None
        assert latest["new"] == "module-1"

        ev = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "story.section_changed")
        ).scalar_one()
        assert ev.scope_id == "US-001"


def test_assign_same_section_twice_is_noop(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Идемпотентность как у update_status: повтор не плодит ревизии."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        story = _make_story(session, pid)
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        row_id = story.row_id

    with transactional(factory) as session:
        history = rev.list_for_entity(session, EntityKind.STORY, row_id)
        section_revs = [r for r in history if json.loads(r.diff)["op"] == "section"]
        assert len(section_revs) == 1


def test_assign_section_none_detaches(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        _make_story(session, pid)
        stories.assign_section(session, story_id="US-001", key="module-1", author="human:test")
        result = stories.assign_section(session, story_id="US-001", key=None, author="human:test")
        assert result is None

    with transactional(factory) as session:
        assert stories.get(session, "US-001").section_id is None


def test_assign_unknown_section_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        _make_story(session, pid)
        with pytest.raises(stories.SectionNotFoundError, match="nope"):
            stories.assign_section(session, story_id="US-001", key="nope", author="human:test")


def test_assign_section_unknown_story_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        with pytest.raises(stories.StoryNotFoundError, match="US-999"):
            stories.assign_section(session, story_id="US-999", key="module-1", author="human:test")


def test_sections_are_scoped_per_project(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        now = datetime.now(UTC)
        p1 = _seed_project(session)
        other = ProjectModel(slug="q", title="Q", root_path="/tmp/q", config_json={})
        other.created = now
        other.updated = now
        session.add(other)
        session.flush()
        p2 = int(other.row_id)

        stories.create_section(
            session, project_id=p1, key="module-1", title="Первый", author="human:test"
        )
        stories.create_section(
            session, project_id=p2, key="module-1", title="Второй", author="human:test"
        )
        assert [s.title for s in stories.list_sections(session, p1)] == ["Первый"]
        assert [s.title for s in stories.list_sections(session, p2)] == ["Второй"]


def test_create_story_with_section_key(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        sec = stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        _make_story(session, pid, section_key="module-1")

    with transactional(factory) as session:
        assert stories.get(session, "US-001").section_id == sec.row_id


def test_create_story_with_unknown_section_key_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Несуществующий ключ — ошибка, а не молча несортированная история."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        with pytest.raises(stories.SectionNotFoundError, match="no-such"):
            _make_story(session, pid, section_key="no-such")


def test_create_writes_section_key_not_row_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """ADO-159: журнал append-only обязан быть читаемым без БД.

    ``row_id`` теряет смысл при переименовании секции, а ``ON DELETE SET
    NULL`` гарантирует, что ссылка однажды повиснет — при том что
    ``revision_revert`` эти diff'ы проигрывает. Проверяется и ревизия, и
    событие: раньше целое число уезжало в оба.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        stories.create_section(
            session, project_id=pid, key="module-1", title="Запасы", author="human:test"
        )
        _make_story(session, pid, section_key="module-1")

    with transactional(factory) as session:
        story = stories.get(session, "US-001")
        history = rev.list_for_entity(session, EntityKind.STORY, story.row_id)
        diff = next(json.loads(r.diff) for r in history if json.loads(r.diff)["op"] == "create")
        assert diff["section"] == "module-1"
        assert "section_id" not in diff

        event = session.execute(
            select(ActivityEventModel).where(ActivityEventModel.kind == "story.created")
        ).scalar_one()
        assert event.payload["section"] == "module-1"
        assert "section_id" not in event.payload


def test_create_without_section_records_null(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Без секции в журнале ключ есть, но пустой — не отсутствует."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        _make_story(session, pid)

    with transactional(factory) as session:
        story = stories.get(session, "US-001")
        history = rev.list_for_entity(session, EntityKind.STORY, story.row_id)
        diff = next(json.loads(r.diff) for r in history if json.loads(r.diff)["op"] == "create")
        assert diff["section"] is None


def test_section_keys_matches_sections_by_id(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Единственная точка резолва: обе формы обязаны отвечать одинаково."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        for i, key in enumerate(("module-1", "module-2")):
            stories.create_section(
                session, project_id=pid, key=key, title=f"T{i}", author="human:test"
            )

    with transactional(factory) as session:
        by_id = stories.sections_by_id(session, pid)
        keys = stories.section_keys(session, pid)
        assert keys == {row_id: sec.key for row_id, sec in by_id.items()}
        assert set(keys.values()) == {"module-1", "module-2"}


# ============================================================================ #
# ADO-159: точечное чтение секции                                              #
# ============================================================================ #


def test_get_section_by_id_reads_one_row(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Секция одной истории берётся одной строкой, а не выгрузкой всего проекта.

    Тест считает ПРОЧИТАННЫЕ СТРОКИ, а не число запросов: первая попытка
    закрыть этот пункт заменила линейный перебор с ``break`` на словарь
    ``sections_by_id`` — запрос стал один, но строк он читает столько же,
    сколько секций в проекте. По числу запросов такая подмена проходит
    незамеченной, по числу строк — нет.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        rows = [
            stories.create_section(
                session, project_id=pid, key=f"module-{i}", title=f"M{i}", author="human:test"
            )
            for i in range(5)
        ]
        target = rows[2]
        assert target.row_id is not None
        target_id = target.row_id

    with transactional(factory) as session:
        got = stories.get_section_by_id(session, target_id)
        assert got is not None
        assert (got.key, got.title) == ("module-2", "M2")

        # Пачечный резолвер остаётся для списков — он ЧИТАЕТ ВСЕ пять.
        assert len(stories.sections_by_id(session, pid)) == 5


def test_get_section_by_id_on_unknown_row_returns_none(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Повисшая ссылка не должна быть исключением: ON DELETE SET NULL это допускает."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _seed_project(session)
        assert stories.get_section_by_id(session, 9999) is None
