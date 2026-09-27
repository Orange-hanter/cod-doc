"""OQM-003: `cod-doc question` — surface parity with the service layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.cli.question import _common
from cod_doc.domain.entities import Priority, QuestionLinkKind, QuestionRelation, QuestionStatus

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / "app.py").write_text("def pay():\n    return 1\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", "p"])
    assert result.exit_code == 0, result.output
    return root


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["question", *args])
    return result.exit_code, result.output


def test_choice_lists_match_domain_enums() -> None:
    """CLI keeps literal choice lists to stay import-light; they must not drift."""
    assert [s.value for s in QuestionStatus] == _common.STATUS_CHOICES
    assert [p.value for p in Priority] == _common.PRIORITY_CHOICES
    assert [k.value for k in QuestionLinkKind] == _common.LINK_KIND_CHOICES
    assert [r.value for r in QuestionRelation] == _common.RELATION_CHOICES


def test_help_lists_every_command() -> None:
    code, out = _run("--help")
    assert code == 0
    for cmd in ("new", "list", "show", "edit", "resolve", "drop", "reopen", "option", "link"):
        assert cmd in out
    for cmd in ("unlink", "verify"):
        assert cmd in out


def test_new_show_json_roundtrip(root: Path) -> None:
    code, out = _run(
        "new",
        "-p",
        "p",
        "-t",
        "Провайдер",
        "-q",
        "Какой?",
        "--option",
        "A",
        "--option",
        "B",
        "--link",
        "code:app.py#pay",
        "--link",
        "task:AB-001:addressed_by",
        "--link",
        "url:https://example.com/x",
        "--json",
    )
    assert code == 0, out
    created = json.loads(out)
    assert created["question_id"] == "Q-001"
    assert [(e["to_kind"], e["to_ref"], e["relation"]) for e in created["links"]] == [
        ("code", "app.py#pay", "about"),
        ("task", "AB-001", "addressed_by"),
        ("url", "https://example.com/x", "about"),
    ]

    code, out = _run("show", "Q-001", "-p", "p", "--json")
    assert code == 0, out
    assert json.loads(out) == created

    code, out = _run("show", "Q-001", "-p", "p")
    assert code == 0
    assert "Q-001 — Провайдер" in out


def test_lifecycle_and_list_filters(root: Path) -> None:
    assert _run("new", "-p", "p", "-t", "T", "-q", "Q?", "--owner", "alice")[0] == 0
    assert _run("option", "add", "Q-001", "Да", "-p", "p")[0] == 0
    assert _run("option", "edit", "Q-001", "0", "-p", "p", "--body", "плюсы")[0] == 0
    code, out = _run("resolve", "Q-001", "-p", "p", "--by", "ADR-002", "--option", "0")
    assert code == 0, out

    code, out = _run("list", "-p", "p", "--json")
    assert json.loads(out) == []
    code, out = _run("list", "-p", "p", "--status", "all", "--linked-to", "adr:ADR-002", "--json")
    rows = json.loads(out)
    assert [(r["question_id"], r["status"]) for r in rows] == [("Q-001", "resolved")]

    code, out = _run("resolve", "Q-001", "-p", "p", "-r", "again")
    assert code == 1
    assert "reopen it first" in out

    assert _run("reopen", "Q-001", "-p", "p")[0] == 0
    assert _run("option", "rm", "Q-001", "0", "-p", "p")[0] == 0
    assert _run("edit", "Q-001", "-p", "p", "--owner", "")[0] == 0
    assert _run("drop", "Q-001", "-p", "p", "--why", "не актуально")[0] == 0
    code, out = _run("show", "Q-001", "-p", "p", "--json")
    card = json.loads(out)
    assert (card["status"], card["owner"], card["options"]) == ("dropped", None, [])


def test_link_verify_exit_code(root: Path) -> None:
    _run("new", "-p", "p", "-t", "T", "-q", "Q?")
    code, out = _run("link", "Q-001", "-p", "p", "--to-kind", "code", "--to-ref", "gone.py")
    assert code == 0
    assert "does not resolve yet" in out
    code, out = _run("verify", "-p", "p", "--json")
    assert code == 1
    assert json.loads(out)["broken"] == 1
    code, _ = _run("unlink", "Q-001", "-p", "p", "--to-kind", "code", "--to-ref", "gone.py")
    assert code == 0
    assert _run("verify", "Q-001", "-p", "p")[0] == 0


def test_errors_are_one_line_not_traceback(root: Path) -> None:
    code, out = _run("show", "Q-404", "-p", "p")
    assert code == 1
    assert "Question 'Q-404' not found." in out
    code, out = _run("new", "-p", "p", "-t", "T", "-q", "Q?", "--link", "nonsense")
    assert code == 2
    code, out = _run("new", "-p", "p", "-t", "T", "-q", "Q?", "--link", "task:not-a-task")
    assert code == 1
    assert "does not look like a task" in out


def test_task_create_addresses(root: Path) -> None:
    runner = CliRunner()
    _run("new", "-p", "p", "-t", "T", "-q", "Q?")
    result = runner.invoke(main, ["plan", "create", "-p", "p", "p-plan", "--principle", "test"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, ["plan", "section", "create", "-p", "p", "p-plan", "A", "S"])
    assert result.exit_code == 0, result.output
    base = ["task", "create", "-p", "p", "--plan", "p-plan", "--section", "A"]
    base += ["--type", "feature", "--priority", "medium", "--prefix", "PP"]
    result = runner.invoke(main, [*base, "--title", "Answer", "--addresses", "Q-001"])
    assert result.exit_code == 0, result.output
    _, out = _run("list", "-p", "p", "--linked-to", "task:PP-001", "--json")
    assert [r["question_id"] for r in json.loads(out)] == ["Q-001"]

    result = runner.invoke(main, [*base, "--title", "Other", "--addresses", "Q-404"])
    assert result.exit_code == 1
    assert "question 'Q-404' not found" in result.output
