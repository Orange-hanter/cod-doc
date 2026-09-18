"""ADO-143 / ADO-145: CLI-поверхность секций и флага ``met``.

Сервисы покрыты в tests/services/test_story_sections.py и test_story_crud.py —
здесь проверяем именно то, что раньше отсутствовало как поверхность: до этих
команд ``set_criterion_met`` не имел ни одного вызывающего.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.services import story_service

if TYPE_CHECKING:
    from pathlib import Path

PROJECT = "sp"
NARRATIVE = "Как управляющий, я хочу алёрты, чтобы не ловить стоп-листы"


def _init_project(tmp_path: Path) -> CliRunner:
    runner = CliRunner()
    root = tmp_path / PROJECT
    root.mkdir()
    result = runner.invoke(main, ["project", "add", str(root), "--name", PROJECT])
    assert result.exit_code == 0, result.output
    return runner


def _read(story_id: str):  # type: ignore[no-untyped-def]
    entry = Config.load().get_project(PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            story = story_service.get(session, story_id)
            acceptance = story_service.list_acceptance(session, story_id)
            return story, acceptance
    finally:
        engine.dispose()


def _make_story(runner: CliRunner, story_id: str = "US-001", *args: str) -> None:
    result = runner.invoke(
        main,
        [
            "story", "create", "-p", PROJECT,
            "--id", story_id,
            "--persona", "Управляющий",
            "--narrative", NARRATIVE,
            "--priority", "high",
            *args,
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output


def test_section_add_and_set_section(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    r = runner.invoke(
        main, ["story", "section", "add", "module-1", "Управление запасами", "-p", PROJECT]
    )
    assert r.exit_code == 0, r.output

    _make_story(runner)
    r = runner.invoke(main, ["story", "set-section", "US-001", "module-1", "-p", PROJECT])
    assert r.exit_code == 0, r.output

    story, _ = _read("US-001")
    assert story is not None
    assert story.section_id is not None


def test_section_add_rejects_unsafe_key(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    r = runner.invoke(main, ["story", "section", "add", "Модуль 1", "Кириллица", "-p", PROJECT])
    assert r.exit_code == 1
    assert "lowercase" in r.output


def test_set_section_unknown_key_fails_with_hint(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    _make_story(runner)
    r = runner.invoke(main, ["story", "set-section", "US-001", "nope", "-p", PROJECT])
    assert r.exit_code == 1
    assert "not found" in r.output


def test_set_section_clear_detaches(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    runner.invoke(main, ["story", "section", "add", "module-1", "Запасы", "-p", PROJECT])
    _make_story(runner)
    runner.invoke(main, ["story", "set-section", "US-001", "module-1", "-p", PROJECT])

    r = runner.invoke(main, ["story", "set-section", "US-001", "--clear", "-p", PROJECT])
    assert r.exit_code == 0, r.output
    story, _ = _read("US-001")
    assert story is not None
    assert story.section_id is None


def test_set_section_rejects_clear_with_key(tmp_path: Path) -> None:
    """Противоречивое намерение — ошибка, а не тихая отвязка."""
    runner = _init_project(tmp_path)
    runner.invoke(main, ["story", "section", "add", "module-1", "Запасы", "-p", PROJECT])
    _make_story(runner)
    runner.invoke(main, ["story", "set-section", "US-001", "module-1", "-p", PROJECT])

    r = runner.invoke(
        main, ["story", "set-section", "US-001", "module-1", "--clear", "-p", PROJECT]
    )
    assert r.exit_code == 1
    assert "конфликтует" in r.output

    story, _ = _read("US-001")
    assert story is not None
    assert story.section_id is not None, "привязка не должна была слететь"


def test_create_with_section_option(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    runner.invoke(main, ["story", "section", "add", "module-1", "Запасы", "-p", PROJECT])
    _make_story(runner, "US-002", "--section", "module-1")

    story, _ = _read("US-002")
    assert story is not None
    assert story.section_id is not None


def test_set_criterion_flips_met(tmp_path: Path) -> None:
    """Ветка, которая до ADO-145 была недостижима ниоткуда."""
    runner = _init_project(tmp_path)
    _make_story(runner, "US-001", "-a", "Первый критерий")

    _, acceptance = _read("US-001")
    assert acceptance[0].met is False

    r = runner.invoke(main, ["story", "set-criterion", "US-001", "0", "-p", PROJECT])
    assert r.exit_code == 0, r.output
    _, acceptance = _read("US-001")
    assert acceptance[0].met is True

    r = runner.invoke(main, ["story", "set-criterion", "US-001", "0", "--not-met", "-p", PROJECT])
    assert r.exit_code == 0, r.output
    _, acceptance = _read("US-001")
    assert acceptance[0].met is False


def test_set_criterion_unknown_position_fails(tmp_path: Path) -> None:
    runner = _init_project(tmp_path)
    _make_story(runner, "US-001", "-a", "Единственный")
    r = runner.invoke(main, ["story", "set-criterion", "US-001", "7", "-p", PROJECT])
    assert r.exit_code == 1
    assert "position 7" in r.output
