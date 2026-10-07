"""ARG-008: `cod-doc adr topic …` и `adr set-topic`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", "p"])
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(
        main, ["adr", "new", "-p", "p", "-t", "PostgreSQL", "--adr-id", "ADR-005"]
    )
    assert result.exit_code == 0, result.output
    return root


def _run(*args: str, input_: str | None = None) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["adr", *args], input=input_)
    return result.exit_code, result.output


def _topics() -> list[dict[str, object]]:
    code, out = _run("topic", "list", "-p", "p", "--json")
    assert code == 0, out
    rows: list[dict[str, object]] = json.loads(out)
    return rows


@pytest.mark.usefixtures("root")
def test_topic_lifecycle() -> None:
    assert _run("topic", "create", "-p", "p", "Хранение", "--includes", "SQLite")[0] == 0
    assert _run("topic", "create", "-p", "p", "Агент")[0] == 0
    assert _run("topic", "move", "-p", "p", "Агент", "0")[0] == 0
    assert [t["name"] for t in _topics()] == ["Агент", "Хранение"]

    code, out = _run("set-topic", "-p", "p", "ADR-005", "Хранение")
    assert code == 0, out
    code, out = _run("show", "-p", "p", "ADR-005", "--json")
    assert json.loads(out)["topic"] == "Хранение"
    assert {t["name"]: t["adr_count"] for t in _topics()} == {"Агент": 0, "Хранение": 1}

    code, out = _run("topic", "update", "-p", "p", "Хранение", "--rename", "Данные")
    assert code == 0, out
    # Удаление полки с решениями спрашивает подтверждение; --yes снимает вопрос.
    code, out = _run("topic", "delete", "-p", "p", "Данные", input_="n\n")
    assert code == 1, out
    assert "Aborted" in out
    assert [t["name"] for t in _topics()] == ["Агент", "Данные"]
    code, out = _run("topic", "delete", "-p", "p", "Данные", "--yes")
    assert code == 0, out
    assert "1 ADR(s) now have no topic" in out
    code, out = _run("show", "-p", "p", "ADR-005", "--json")
    assert json.loads(out)["topic"] is None


@pytest.mark.usefixtures("root")
def test_set_topic_errors() -> None:
    code, out = _run("set-topic", "-p", "p", "ADR-005", "Нет такой")
    assert code == 1
    assert "not found" in out
    code, out = _run("set-topic", "-p", "p", "ADR-005")
    assert code == 2
    assert "exactly one" in out
    code, out = _run("set-topic", "-p", "p", "ADR-005", "  ")
    assert code == 2
    assert "exactly one" in out
    code, out = _run("topic", "create", "-p", "p", "A")
    assert code == 0, out
    code, out = _run("topic", "create", "-p", "p", "A")
    assert code == 2
    assert "already exists" in out


@pytest.mark.usefixtures("root")
def test_topic_output_escapes_rich_markup() -> None:
    """Разметка в имени и составе полки печатается буквально, Rich её не исполняет."""
    name = "[red]x[/red]"
    code, out = _run("topic", "create", "-p", "p", name, "--includes", "[link=http://e]y[/link]")
    assert code == 0, out
    assert name in out
    code, out = _run("topic", "list", "-p", "p")
    assert code == 0, out
    assert name in out
    assert "[link=http://e]y[/link]" in out
