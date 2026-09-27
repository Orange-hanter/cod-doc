"""ADO-204 (RFC 26 §5): CLI `plan section create|list|update|move|rm` поверх plan_service.

Семантика сервиса покрыта сервисными тестами; здесь — что команды пишут
секцию с ревизией и событием, читают только свой план, а отказы доносят
сообщением без трейсбека. Эталоны — литералы и прямые SELECT по
plan_section, revision, activity_event.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result
from sqlalchemy import text

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, ProjectModel
from cod_doc.services import task_service

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "tpsc"
_SCOPE = "plan-x"


def _invoke(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(main, args, env={"FORCE_COLOR": ""})


def _seed(tmp_path: Path) -> None:
    """Проект в реестре и пустой план plan-x, заведённый командой `plan create`."""
    root = tmp_path / _PROJECT
    root.mkdir()
    added = _invoke(["project", "add", str(root), "--name", _PROJECT])
    assert added.exit_code == 0, added.output
    created = _invoke(["plan", "create", _SCOPE, "-p", _PROJECT, "--principle", "P"])
    assert created.exit_code == 0, created.output


def _query(sql: str, **params: object) -> list[tuple[object, ...]]:
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            rows = session.execute(text(sql), params).all()
    finally:
        engine.dispose()
    return [tuple(r) for r in rows]


def _section(args: list[str]) -> Result:
    return _invoke(["plan", "section", *args, "-p", _PROJECT])


def test_section_create_json(tmp_path: Path) -> None:
    """--json: буква в верхнем регистре, слаг, позиция 0; ревизия и событие."""
    _seed(tmp_path)
    result = _section(["create", _SCOPE, "f", "Structure protocol (RFC 24)", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["letter"] == "F"
    assert data["slug"] == "F-Structure-protocol-RFC-24"
    assert data["position"] == 0
    assert data["plan_scope"] == "plan-x"
    assert data["title"] == "Structure protocol (RFC 24)"
    assert "warnings" not in data

    rows = _query("SELECT row_id, letter, title, slug, position FROM plan_section")
    assert len(rows) == 1
    section_row_id = rows[0][0]
    assert rows[0][1:] == ("F", "Structure protocol (RFC 24)", "F-Structure-protocol-RFC-24", 0)
    assert data["section_id"] == section_row_id
    assert _query("SELECT entity_id, author FROM revision WHERE entity_kind = 'plan_section'") == [
        (section_row_id, "cli")
    ]
    assert _query("SELECT count(*) FROM activity_event WHERE kind = 'plan.section_created'") == [
        (1,)
    ]


def test_section_create_html_warning(tmp_path: Path) -> None:
    """HTML-сущность в заголовке — предупреждение, текст в БД не заменён."""
    _seed(tmp_path)
    result = _section(["create", _SCOPE, "Q", "Q&amp;A", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(data["warnings"]) == 1
    assert "&amp;" in data["warnings"][0]
    assert _query("SELECT title FROM plan_section") == [("Q&amp;A",)]


def test_section_create_human_mode_warning(tmp_path: Path) -> None:
    """Без --json — строка с буквой и слагом, предупреждение отдельной строкой."""
    _seed(tmp_path)
    result = _section(["create", _SCOPE, "Q", "Q&amp;A"])
    assert result.exit_code == 0, result.output
    assert "Секция Q" in result.output
    assert "&amp;" in result.output
    assert _query("SELECT count(*) FROM plan_section") == [(1,)]


def test_section_create_errors_are_messages(tmp_path: Path) -> None:
    """Повтор буквы, отрицательная позиция, неизвестный план — exit 1 без трейсбека."""
    _seed(tmp_path)
    first = _section(["create", _SCOPE, "F", "First"])
    assert first.exit_code == 0, first.output

    dup = _section(["create", _SCOPE, "f", "Again"])
    assert dup.exit_code == 1, dup.output
    assert "plan section already exists: 'F'" in dup.output
    assert "Traceback" not in dup.output

    bad_pos = _section(["create", _SCOPE, "G", "Neg", "--position", "-1"])
    assert bad_pos.exit_code == 1, bad_pos.output
    assert "Validation error" in bad_pos.output
    assert "Traceback" not in bad_pos.output

    no_plan = _section(["create", "plan-nope", "H", "Missing"])
    assert no_plan.exit_code == 1, no_plan.output
    assert "Plan 'plan-nope' not found in project" in no_plan.output
    assert "Traceback" not in no_plan.output

    assert _query("SELECT count(*) FROM plan_section") == [(1,)]
    assert _query("SELECT count(*) FROM revision WHERE entity_kind = 'plan_section'") == [(1,)]


def test_section_list_json(tmp_path: Path) -> None:
    """Порядок хранения и ровно пять ключей на элемент."""
    _seed(tmp_path)
    for letter, title in (("A", "Alpha"), ("B", "Beta")):
        created = _section(["create", _SCOPE, letter, title])
        assert created.exit_code == 0, created.output

    result = _section(["list", _SCOPE, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [e["letter"] for e in data] == ["A", "B"]
    assert [e["title"] for e in data] == ["Alpha", "Beta"]
    assert [e["position"] for e in data] == [0, 1]
    for e in data:
        assert set(e) == {"letter", "title", "slug", "position", "doc_id"}
        assert e["doc_id"] is None


def test_section_list_human_mode(tmp_path: Path) -> None:
    """Без --json — таблица с буквой и заголовком."""
    _seed(tmp_path)
    created = _section(["create", _SCOPE, "A", "Alpha"])
    assert created.exit_code == 0, created.output
    result = _section(["list", _SCOPE])
    assert result.exit_code == 0, result.output
    assert "Alpha" in result.output


def test_section_list_foreign_plan(tmp_path: Path) -> None:
    """План другого проекта в той же БД не читается — exit 1 без трейсбека."""
    _seed(tmp_path)
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            other = ProjectModel(slug="other", title="Other", root_path=str(tmp_path / "o"))
            session.add(other)
            session.flush()
            session.add(PlanModel(project_id=other.row_id, scope="plan-foreign"))
    finally:
        engine.dispose()

    result = _section(["list", "plan-foreign"])
    assert result.exit_code == 1, result.output
    assert "Plan 'plan-foreign' not found in project" in result.output
    assert "Traceback" not in result.output

    unknown = _section(["list", "plan-nope"])
    assert unknown.exit_code == 1, unknown.output
    assert "Traceback" not in unknown.output


def _seed_abcd(tmp_path: Path) -> None:
    """План plan-x с секциями A, B, C, D, заведёнными `plan section create`."""
    _seed(tmp_path)
    for letter in "ABCD":
        created = _section(["create", _SCOPE, letter, f"Section {letter}"])
        assert created.exit_code == 0, created.output


def _seed_tasks(letter: str, task_ids: list[str]) -> None:
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            pid, plan_id, section_id = session.execute(
                text(
                    "SELECT p.project_id, p.row_id, s.row_id FROM plan p "
                    "JOIN plan_section s ON s.plan_id = p.row_id "
                    "WHERE p.scope = :scope AND s.letter = :letter"
                ),
                {"scope": _SCOPE, "letter": letter},
            ).one()
            for task_id in task_ids:
                task_service.create(
                    session,
                    project_id=pid,
                    plan_id=plan_id,
                    section_id=section_id,
                    title=f"Task {task_id}",
                    type=TaskType.FEATURE,
                    priority=Priority.MEDIUM,
                    author="test",
                    task_id=task_id,
                )
    finally:
        engine.dispose()


def _section_revisions() -> int:
    rows = _query("SELECT count(*) FROM revision WHERE entity_kind = 'plan_section'")
    count = rows[0][0]
    assert isinstance(count, int)
    return count


def test_section_update_title(tmp_path: Path) -> None:
    """Новый заголовок, слаг прежний, +1 ревизия; повтор ревизий не добавляет."""
    _seed_abcd(tmp_path)
    assert _section_revisions() == 4
    args = ["update", _SCOPE, "A", "--title", "New title", "--reason", "r", "--json"]

    result = _section(args)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["title"] == "New title"
    assert data["slug"] == "A-Section-A"
    assert data["letter"] == "A"
    assert data["plan_scope"] == "plan-x"
    assert data["position"] == 0
    assert data["doc_id"] is None
    assert "warnings" not in data
    assert _query("SELECT title, slug FROM plan_section WHERE letter = 'A'") == [
        ("New title", "A-Section-A")
    ]
    assert _section_revisions() == 5

    again = _section(args)
    assert again.exit_code == 0, again.output
    assert json.loads(again.output)["title"] == "New title"
    assert _section_revisions() == 5


def test_section_update_human_mode_warning(tmp_path: Path) -> None:
    """Без --json — зелёная строка и предупреждение про HTML-сущность."""
    _seed_abcd(tmp_path)
    result = _section(["update", _SCOPE, "A", "--title", "Q&amp;A", "--reason", "r"])
    assert result.exit_code == 0, result.output
    assert "Секция A" in result.output
    assert "&amp;" in result.output
    assert _query("SELECT title FROM plan_section WHERE letter = 'A'") == [("Q&amp;A",)]


def test_section_reason_required(tmp_path: Path) -> None:
    """update/move/rm без --reason — ошибка click, данные не тронуты."""
    _seed_abcd(tmp_path)
    before = _query("SELECT letter, title, slug, position FROM plan_section ORDER BY letter")
    assert before == [
        ("A", "Section A", "A-Section-A", 0),
        ("B", "Section B", "B-Section-B", 1),
        ("C", "Section C", "C-Section-C", 2),
        ("D", "Section D", "D-Section-D", 3),
    ]
    for args in (
        ["update", _SCOPE, "A", "--title", "X"],
        ["move", _SCOPE, "D", "--before", "A"],
        ["rm", _SCOPE, "D"],
    ):
        result = _section(args)
        assert result.exit_code != 0, result.output
        assert "Traceback" not in result.output
    assert _query("SELECT letter, title, slug, position FROM plan_section ORDER BY letter") == [
        ("A", "Section A", "A-Section-A", 0),
        ("B", "Section B", "B-Section-B", 1),
        ("C", "Section C", "C-Section-C", 2),
        ("D", "Section D", "D-Section-D", 3),
    ]
    assert _section_revisions() == 4


def test_section_move_before(tmp_path: Path) -> None:
    """D перед B: порядок A, D, B, C с плотными позициями — в ответе и в БД."""
    _seed_abcd(tmp_path)
    result = _section(["move", _SCOPE, "D", "--before", "B", "--reason", "r", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["plan_scope"] == "plan-x"
    assert data["letter"] == "D"
    assert [(e["letter"], e["position"]) for e in data["order"]] == [
        ("A", 0),
        ("D", 1),
        ("B", 2),
        ("C", 3),
    ]
    assert _query("SELECT letter, position FROM plan_section ORDER BY position") == [
        ("A", 0),
        ("D", 1),
        ("B", 2),
        ("C", 3),
    ]


def test_section_move_human_mode(tmp_path: Path) -> None:
    """Без --json — порядок букв одной строкой."""
    _seed_abcd(tmp_path)
    result = _section(["move", _SCOPE, "A", "--after", "C", "--reason", "r"])
    assert result.exit_code == 0, result.output
    assert "B C A D" in result.output


def test_section_move_bad_args(tmp_path: Path) -> None:
    """Нет якоря или неизвестная буква — exit 1 сообщением, порядок прежний."""
    _seed_abcd(tmp_path)
    none_given = _section(["move", _SCOPE, "D", "--reason", "r"])
    assert none_given.exit_code == 1, none_given.output
    assert "exactly one of before, after, position" in none_given.output
    assert "Traceback" not in none_given.output

    unknown = _section(["move", _SCOPE, "D", "--before", "Z", "--reason", "r"])
    assert unknown.exit_code == 1, unknown.output
    assert "plan section not found: 'Z'" in unknown.output
    assert "Traceback" not in unknown.output

    assert _query("SELECT letter, position FROM plan_section ORDER BY position") == [
        ("A", 0),
        ("B", 1),
        ("C", 2),
        ("D", 3),
    ]
    assert _section_revisions() == 4


def test_section_rm_requires_reassign(tmp_path: Path) -> None:
    """Непустая секция без --reassign-to — отказ; с ним задачи переезжают в C."""
    _seed_abcd(tmp_path)
    _seed_tasks("B", ["TS-001", "TS-002"])

    refused = _section(["rm", _SCOPE, "B", "--reason", "r"])
    assert refused.exit_code == 1, refused.output
    assert "--reassign-to" in refused.output
    assert "Traceback" not in refused.output
    assert _query("SELECT count(*) FROM plan_section WHERE letter = 'B'") == [(1,)]

    result = _section(["rm", _SCOPE, "B", "--reassign-to", "C", "--reason", "r", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == {
        "plan_scope": "plan-x",
        "letter": "B",
        "deleted": True,
        "reassign_to": "C",
        "moved_tasks": 2,
    }
    assert _query("SELECT letter FROM plan_section ORDER BY position") == [("A",), ("C",), ("D",)]
    assert _query(
        "SELECT t.task_id, s.letter FROM task t "
        "JOIN plan_section s ON s.row_id = t.section_id ORDER BY t.task_id"
    ) == [("TS-001", "C"), ("TS-002", "C")]


def test_section_rm_empty_human_mode(tmp_path: Path) -> None:
    """Пустая секция удаляется без --reassign-to."""
    _seed_abcd(tmp_path)
    result = _section(["rm", _SCOPE, "D", "--reason", "r"])
    assert result.exit_code == 0, result.output
    assert "Секция D удалена" in result.output
    assert _query("SELECT letter FROM plan_section ORDER BY position") == [("A",), ("B",), ("C",)]


def test_section_rm_has_no_force() -> None:
    """Флага --force нет: CASCADE снёс бы задачи секции."""
    result = _invoke(["plan", "section", "rm", "--help"])
    assert result.exit_code == 0, result.output
    assert "--force" not in result.output
