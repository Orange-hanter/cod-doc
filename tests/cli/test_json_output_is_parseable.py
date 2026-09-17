"""`--json` обязан оставаться JSON в пайпе.

`rich.Console.print` переносит строку по ширине терминала, а в пайпе ширина
считается 80. Перенос попадает ВНУТРЬ строкового значения, и вывод перестаёт
парситься — но только когда конкретное значение длиннее остатка строки.
Дефект плавающий, выглядит как порча данных и до ADO-176 не покрывался ничем.

Здесь два уровня: AST-гейт против повторного появления `console.print` над
`json.dumps` и функциональная проверка, что длинный заголовок переживает
узкий терминал. Гейт важнее: именно он нашёл 21 место из 30 — многострочные
вызовы `console.print(\\n    json.dumps(...))`, которые grep не видит.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import task_service

if TYPE_CHECKING:
    from collections.abc import Iterator

CLI_ROOT = Path(__file__).resolve().parents[2] / "cod_doc" / "cli"

#: Длиннее 80 колонок: ровно на такой длине rich и ставил перенос.
_LONG_TITLE = (
    "Migration: plan + plan_section + task + dependency + affected_file "
    "и ещё немного текста, чтобы гарантированно перевалить за восемьдесят колонок"
)

#: Узкий терминал — худший случай для переноса.
_NARROW = "40"

#: Похоже на разметку rich: `[bold]…[/bold]` он ПОГЛОЩАЕТ, и вывод при этом
#: остаётся валидным JSON — значение молча теряет кусок текста.
_MARKUPY_TITLE = "fix [bold]critical[/bold] path"

#: Непарный закрывающий тег: на нём `console.print` не искажает вывод, а падает
#: с MarkupError, то есть команда завершается ошибкой на валидных данных.
_UNBALANCED_TITLE = "cleanup [/] marker"

_PROJECT = "jsonpipe"


def _rich_printed_dumps(tree: ast.AST) -> Iterator[int]:
    """Номера строк, где `console.print(...)` печатает `<...>.dumps(...)`."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "print"
            and isinstance(func.value, ast.Name)
            and func.value.id == "console"
        ):
            continue
        arg = node.args[0]
        if (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "dumps"
        ):
            yield node.lineno


def test_no_json_is_printed_through_rich() -> None:
    """AST-гейт: машинный вывод идёт через click.echo, но не через rich."""
    offenders: list[str] = []
    for path in sorted(CLI_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders += [
            f"{path.relative_to(CLI_ROOT.parents[1])}:{lineno}"
            for lineno in _rich_printed_dumps(tree)
        ]
    assert not offenders, (
        "rich.Console переносит строку по ширине терминала и рвёт JSON внутри "
        "строкового значения. Печатай машинный вывод через click.echo:\n  " + "\n  ".join(offenders)
    )


def _make_project(tmp_path: Path, titles: list[str]) -> str:
    """Зарегистрированный проект с задачами, чьи заголовки заданы."""
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output

    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory) as session:
            project = ProjectRepository(session).get_by_slug(_PROJECT)
            assert project is not None and project.row_id is not None
            now = datetime.now(UTC)
            plan = PlanModel(
                project_id=project.row_id, scope=f"{_PROJECT}-plan", created=now, last_updated=now
            )
            session.add(plan)
            session.flush()
            section = PlanSectionModel(
                plan_id=plan.row_id, letter="A", title="Core", slug="A-Core", position=0
            )
            session.add(section)
            session.flush()
            for title in titles:
                task_service.create(
                    session,
                    project_id=project.row_id,
                    plan_id=plan.row_id,
                    section_id=section.row_id,
                    title=title,
                    type=TaskType.FEATURE,
                    priority=Priority.LOW,
                    id_prefix="JSN",
                    author="test",
                )
    finally:
        engine.dispose()
    return _PROJECT


def _titles(project: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("COLUMNS", _NARROW)
    result = CliRunner().invoke(main, ["task", "list", "-p", project, "--json"])
    assert result.exit_code == 0, result.output
    return [t["title"] for t in json.loads(result.output)]


def test_long_title_survives_a_narrow_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перенос по ширине терминала рвал JSON внутри строкового значения."""
    project = _make_project(tmp_path, [_LONG_TITLE])
    assert _titles(project, monkeypatch) == [_LONG_TITLE]


def test_markup_like_title_is_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Худший режим: rich ПОГЛОЩАЕТ `[bold]…[/bold]`, а вывод остаётся валидным.

    Значение молча теряет кусок текста — данные искажены, но ни одна проверка
    на парсимость этого не увидит.
    """
    project = _make_project(tmp_path, [_MARKUPY_TITLE])
    assert _titles(project, monkeypatch) == [_MARKUPY_TITLE]


def test_unbalanced_markup_title_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Непарный `[/]` роняет `console.print` с MarkupError на валидных данных."""
    project = _make_project(tmp_path, [_UNBALANCED_TITLE])
    assert _titles(project, monkeypatch) == [_UNBALANCED_TITLE]
