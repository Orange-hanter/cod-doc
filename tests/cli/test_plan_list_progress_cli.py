"""AFT-007 (RFC 27 F8): CLI `plan list` и `plan progress [--all [--by-section]]`.

Проект ``tpcc`` заводится через ``project add`` под изолированным
COD_DOC_HOME; в ту же БД кладётся второй проект ``other`` с планом ``plan-z``
для проверки изоляции. Статусы задач пишутся прямо в ``TaskModel``, эталоны —
литералы сида. Единственное сравнение режимов между собой —
``test_cli_all_matches_single``: совпадение ``--all --by-section`` с одиночным
режимом и есть предмет критерия приёмки 1.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from click.testing import CliRunner, Result
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "tpcc"
_OTHER = "other"

# (project, scope, [(letter, [status, ...]), ...]) — порядок = порядок создания.
_SEED: list[tuple[str, str, list[tuple[str, list[str]]]]] = [
    (_PROJECT, "plan-x", [("A", ["done", "todo"]), ("B", ["cancelled"])]),
    (_PROJECT, "plan-y", [("C", ["todo"])]),
    (_OTHER, "plan-z", [("A", ["done"])]),
]


def _seed(tmp_path: Path) -> None:
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output

    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    now = datetime.now(UTC)
    try:
        with transactional(factory) as s:
            ids = {
                _PROJECT: s.execute(
                    select(ProjectModel.row_id).where(ProjectModel.slug == _PROJECT)
                ).scalar_one(),
            }
            other = ProjectModel(
                slug=_OTHER, title=_OTHER, root_path="/tmp/other", created=now, updated=now
            )
            s.add(other)
            s.flush()
            ids[_OTHER] = other.row_id

            task_no = 0
            for project, scope, sections in _SEED:
                plan = PlanModel(
                    project_id=ids[project],
                    scope=scope,
                    principle=f"principle of {scope}",
                    created=now,
                    last_updated=now,
                )
                s.add(plan)
                s.flush()
                for position, (letter, statuses) in enumerate(sections):
                    sec = PlanSectionModel(
                        plan_id=plan.row_id,
                        letter=letter,
                        title=f"Section {letter}",
                        slug=f"{letter}-Section",
                        position=position,
                    )
                    s.add(sec)
                    s.flush()
                    for status in statuses:
                        task_no += 1
                        s.add(
                            TaskModel(
                                project_id=ids[project],
                                task_id=f"T-{task_no:03d}",
                                plan_id=plan.row_id,
                                section_id=sec.row_id,
                                title=f"task {task_no}",
                                status=status,
                                type="feature",
                                priority="medium",
                                created=now,
                                last_updated=now,
                            )
                        )
    finally:
        engine.dispose()


def _run(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(main, ["plan", *args], env={"FORCE_COLOR": ""})


def _json_of(args: list[str]) -> Any:
    result = _run(args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_cli_plan_list_json(tmp_path: Path) -> None:
    _seed(tmp_path)
    data = _json_of(["list", "-p", _PROJECT, "--json"])
    assert [r["scope"] for r in data] == ["plan-x", "plan-y"]
    assert "plan-z" not in [r["scope"] for r in data]
    x, y = data
    assert (x["status"], x["total"], x["done"], x["remaining"]) == ("in-progress", 3, 1, 1)
    assert (y["status"], y["total"], y["done"], y["remaining"]) == ("pending", 1, 0, 1)
    assert x["principle"] == "principle of plan-x"


def test_cli_plan_list_status_filter(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert _json_of(["list", "-p", _PROJECT, "--status", "empty", "--json"]) == []
    pending = _json_of(["list", "-p", _PROJECT, "--status", "pending", "--json"])
    assert [r["scope"] for r in pending] == ["plan-y"]
    started = _json_of(["list", "-p", _PROJECT, "--status", "in_progress", "--json"])
    assert [r["scope"] for r in started] == ["plan-x"]

    bogus = _run(["list", "-p", _PROJECT, "--status", "bogus"])
    assert bogus.exit_code == 1, bogus.output
    assert "Unknown plan status 'bogus'" in bogus.output
    assert "Traceback" not in bogus.output


def test_cli_plan_list_human(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = _run(["list", "-p", _PROJECT])
    assert result.exit_code == 0, result.output
    assert "plan-x" in result.output
    assert "plan-y" in result.output
    assert "plan-z" not in result.output


def test_cli_progress_all_by_section_json(tmp_path: Path) -> None:
    _seed(tmp_path)
    data = _json_of(["progress", "-p", _PROJECT, "--all", "--by-section", "--json"])
    assert data["project"] == "tpcc"
    plans = data["plans"]
    assert [p["scope"] for p in plans] == ["plan-x", "plan-y"]
    x, y = plans
    assert [s["letter"] for s in x["sections"]] == ["A", "B"]
    sec_a, sec_b = x["sections"]
    assert (sec_a["total"], sec_a["done"], sec_a["remaining"]) == (2, 1, 1)
    assert (sec_b["total"], sec_b["cancelled"], sec_b["remaining"]) == (1, 1, 0)
    assert [s["letter"] for s in y["sections"]] == ["C"]
    assert (y["total"], y["done"], y["remaining"]) == (1, 0, 1)


def test_cli_all_matches_single(tmp_path: Path) -> None:
    _seed(tmp_path)
    data = _json_of(["progress", "-p", _PROJECT, "--all", "--by-section", "--json"])
    plans = data["plans"]
    assert [p["scope"] for p in plans] == ["plan-x", "plan-y"]
    for row in plans:
        single = _json_of(["progress", row["scope"], "-p", _PROJECT, "--json"])
        assert set(row) == set(single)
        for key in single:
            assert row[key] == single[key], (row["scope"], key)
    assert plans[0]["total"] == 3
    assert (plans[0]["done"], plans[0]["cancelled"], plans[0]["remaining"]) == (1, 1, 1)


def test_cli_progress_all_without_sections(tmp_path: Path) -> None:
    _seed(tmp_path)
    data = _json_of(["progress", "-p", _PROJECT, "--all", "--json"])
    plans = data["plans"]
    assert [p["scope"] for p in plans] == ["plan-x", "plan-y"]
    assert all("sections" not in p for p in plans)
    assert set(plans[0]) == {
        "scope",
        "total",
        "done",
        "in_progress",
        "cancelled",
        "remaining",
        "status",
    }


def test_cli_progress_human(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = _run(["progress", "-p", _PROJECT, "--all", "--by-section"])
    assert result.exit_code == 0, result.output
    assert "plan-x" in result.output
    assert "A: Section A" in result.output
    assert "plan-z" not in result.output


def test_cli_progress_unknown_scope_lists_available(tmp_path: Path) -> None:
    _seed(tmp_path)
    result = _run(["progress", "nope", "-p", _PROJECT])
    assert result.exit_code == 1, result.output
    assert "Plan 'nope' not found" in result.output
    assert "Available plan scopes: plan-x, plan-y" in result.output
    assert "Traceback" not in result.output

    # Чужой план — та же ошибка, план другого проекта не показывается.
    foreign = _run(["progress", "plan-z", "-p", _PROJECT])
    assert foreign.exit_code == 1, foreign.output
    assert "Plan 'plan-z' not found" in foreign.output
    assert "Traceback" not in foreign.output


def test_cli_progress_usage_errors(tmp_path: Path) -> None:
    _seed(tmp_path)
    for args in (
        ["progress", "plan-x", "-p", _PROJECT, "--all"],
        ["progress", "-p", _PROJECT],
        ["progress", "plan-x", "-p", _PROJECT, "--by-section"],
    ):
        result = _run(args)
        assert result.exit_code != 0, (args, result.output)
        assert "Traceback" not in result.output
