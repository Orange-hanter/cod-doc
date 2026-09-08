"""ADO-143 / ADO-140: секции на списке Stories и сортировка карточек.

До этого секция выводилась из прозы нарратива, на русском корпусе не выводилась
никогда, и все истории висели в одной корзине «Unsorted». Сортировки не было
вовсе: ``?sort=`` роут не читал.
"""

from __future__ import annotations

import pytest

from cod_doc.domain.entities import Priority, UserStoryStatus
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.services import story_service

from .test_web_stories import stories_client  # noqa: F401 — переиспользуем фикстуру


def _seed(entry, project_db_id, rows, sections=()):  # type: ignore[no-untyped-def]
    """rows: (story_id, persona, narrative, priority, status, section_key|None)."""
    engine = make_engine(f"sqlite:///{entry.cod_doc_dir / 'state.db'}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        for key, title in sections:
            story_service.create_section(
                session, project_id=project_db_id, key=key, title=title, author="human:test"
            )
        for sid, persona, narrative, priority, status, section_key in rows:
            story_service.create(
                session,
                project_id=project_db_id,
                story_id=sid,
                persona=persona,
                narrative=narrative,
                priority=priority,
                author="human:test",
                status=status,
            )
            if section_key:
                story_service.assign_section(
                    session, story_id=sid, key=section_key, author="human:test"
                )
    engine.dispose()


RU = "Как управляющий, я хочу X, чтобы Y"


def test_stored_sections_replace_the_unsorted_bucket(stories_client) -> None:  # noqa: F811
    client, entry, pid, _ = stories_client
    _seed(
        entry,
        pid,
        [
            ("US-001", "Управляющий", RU, Priority.HIGH, UserStoryStatus.ACCEPTED, "module-1"),
            ("US-002", "Финконтролёр", RU, Priority.LOW, UserStoryStatus.ACCEPTED, "module-2"),
        ],
        sections=[("module-1", "Управление запасами"), ("module-2", "Закупки")],
    )

    r = client.get(f"/p/{entry.name}/stories?group_by=section")
    assert r.status_code == 200
    # Человеческие названия, а не «Section 1».
    assert "Управление запасами" in r.text
    assert "Закупки" in r.text
    assert "Unsorted" not in r.text


def test_story_without_section_lands_in_no_section_bucket(stories_client) -> None:  # noqa: F811
    client, entry, pid, _ = stories_client
    _seed(entry, pid, [("US-003", "Гость", RU, Priority.LOW, UserStoryStatus.DRAFT, None)])

    r = client.get(f"/p/{entry.name}/stories?group_by=section")
    assert r.status_code == 200
    assert "No section" in r.text
    assert "Unsorted" not in r.text


def test_sections_render_in_registry_order(stories_client) -> None:  # noqa: F811
    """Порядок групп — по position секции, а не по алфавиту ключа."""
    client, entry, pid, _ = stories_client
    _seed(
        entry,
        pid,
        [
            ("US-001", "A", RU, Priority.HIGH, UserStoryStatus.ACCEPTED, "zebra"),
            ("US-002", "B", RU, Priority.HIGH, UserStoryStatus.ACCEPTED, "alpha"),
        ],
        # zebra заведена первой, значит position=1 и она должна быть выше alpha.
        sections=[("zebra", "Первая секция"), ("alpha", "Вторая секция")],
    )

    r = client.get(f"/p/{entry.name}/stories?group_by=section")
    assert r.text.index("Первая секция") < r.text.index("Вторая секция")


@pytest.mark.parametrize("sort_key", ["id", "priority", "status", "updated"])
def test_sort_param_is_accepted(stories_client, sort_key: str) -> None:  # noqa: F811
    client, entry, pid, _ = stories_client
    _seed(entry, pid, [("US-001", "A", RU, Priority.LOW, UserStoryStatus.DRAFT, None)])
    r = client.get(f"/p/{entry.name}/stories?sort={sort_key}")
    assert r.status_code == 200


def test_sort_by_priority_puts_critical_first(stories_client) -> None:  # noqa: F811
    client, entry, pid, _ = stories_client
    _seed(
        entry,
        pid,
        [
            ("US-001", "Low one", RU, Priority.LOW, UserStoryStatus.ACCEPTED, None),
            ("US-002", "Critical one", RU, Priority.CRITICAL, UserStoryStatus.ACCEPTED, None),
        ],
    )

    default = client.get(f"/p/{entry.name}/stories?group_by=none")
    assert default.text.index("US-001") < default.text.index("US-002")

    by_prio = client.get(f"/p/{entry.name}/stories?group_by=none&sort=priority")
    assert by_prio.text.index("US-002") < by_prio.text.index("US-001")


def test_unknown_sort_falls_back_to_id(stories_client) -> None:  # noqa: F811
    client, entry, pid, _ = stories_client
    _seed(
        entry,
        pid,
        [
            ("US-001", "A", RU, Priority.LOW, UserStoryStatus.ACCEPTED, None),
            ("US-002", "B", RU, Priority.CRITICAL, UserStoryStatus.ACCEPTED, None),
        ],
    )
    r = client.get(f"/p/{entry.name}/stories?group_by=none&sort=nonsense")
    assert r.status_code == 200
    assert r.text.index("US-001") < r.text.index("US-002")


def test_group_toggle_keeps_sort_and_vice_versa(stories_client) -> None:  # noqa: F811
    """Смена Group не должна сбрасывать Sort — ссылки несут оба параметра."""
    client, entry, pid, _ = stories_client
    _seed(entry, pid, [("US-001", "A", RU, Priority.LOW, UserStoryStatus.DRAFT, None)])

    r = client.get(f"/p/{entry.name}/stories?sort=priority")
    assert "group_by=persona&amp;sort=priority" in r.text

    r2 = client.get(f"/p/{entry.name}/stories?group_by=persona")
    assert "sort=priority&amp;group_by=persona" in r2.text


def test_delivered_chip_counts_a_real_status(stories_client) -> None:  # noqa: F811
    """Чип считал статус 'active', которого нет в UserStoryStatus, — всегда 0."""
    client, entry, pid, _ = stories_client
    _seed(
        entry,
        pid,
        [
            ("US-001", "A", RU, Priority.HIGH, UserStoryStatus.DELIVERED, None),
            ("US-002", "B", RU, Priority.HIGH, UserStoryStatus.DRAFT, None),
        ],
    )
    r = client.get(f"/p/{entry.name}/stories")
    assert "1 delivered" in r.text
    assert "1 draft" in r.text
    assert "active</span>" not in r.text


def test_russian_narrative_now_renders_want_and_so_that(stories_client) -> None:  # noqa: F811
    """ADO-144: карточка перестала быть сырым абзацем на русском корпусе."""
    client, entry, pid, _ = stories_client
    _seed(
        entry,
        pid,
        [
            (
                "US-001",
                "Управляющий",
                "Как управляющий, я хочу получать алёрты, чтобы избегать стоп-листов",
                Priority.HIGH,
                UserStoryStatus.ACCEPTED,
                None,
            )
        ],
    )
    r = client.get(f"/p/{entry.name}/stories")
    assert "Хочет" in r.text
    assert "получать алёрты" in r.text
    assert "избегать стоп-листов" in r.text
