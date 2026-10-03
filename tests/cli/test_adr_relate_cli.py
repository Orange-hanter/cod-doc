"""ARG-001/ARG-002: `cod-doc adr relate|unrelate` и `adr sync --clear-decided-at`."""

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
    for adr_id, title in (("ADR-005", "PostgreSQL"), ("ADR-010", "Два профиля")):
        result = CliRunner().invoke(
            main, ["adr", "new", "-p", "p", "-t", title, "--adr-id", adr_id]
        )
        assert result.exit_code == 0, result.output
    return root


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, ["adr", *args])
    return result.exit_code, result.output


def _show(adr_id: str) -> dict[str, object]:
    code, out = _run("show", "-p", "p", adr_id, "--json")
    assert code == 0, out
    payload: dict[str, object] = json.loads(out)
    return payload


@pytest.mark.usefixtures("root")
def test_relate_shows_up_on_both_sides() -> None:
    code, out = _run(
        "relate", "-p", "p", "ADR-010", "ADR-005", "--kind", "amends", "--reason", "уточняет"
    )
    assert code == 0, out

    assert _show("ADR-010")["relations"] == {
        "outgoing": [
            {
                "adr_id": "ADR-005",
                "title": "PostgreSQL",
                "status": "proposed",
                "kind": "amends",
                "reason": "уточняет",
            }
        ],
        "incoming": [],
    }
    code, out = _run("show", "-p", "p", "ADR-005")
    assert code == 0, out
    assert "amended by" in out
    assert "ADR-010" in out


@pytest.mark.usefixtures("root")
def test_relate_repeat_exits_2_with_message() -> None:
    assert _run("relate", "-p", "p", "ADR-010", "ADR-005", "--kind", "amends")[0] == 0
    code, out = _run("relate", "-p", "p", "ADR-010", "ADR-005", "--kind", "amends")
    assert code == 2
    assert "already amends" in out


@pytest.mark.usefixtures("root")
def test_unrelate_removes_and_missing_exits_1() -> None:
    assert _run("relate", "-p", "p", "ADR-010", "ADR-005", "--kind", "depends_on")[0] == 0
    code, out = _run("unrelate", "-p", "p", "ADR-010", "ADR-005", "--kind", "depends_on")
    assert code == 0, out
    assert _show("ADR-010")["relations"] == {"outgoing": [], "incoming": []}
    code, out = _run("unrelate", "-p", "p", "ADR-010", "ADR-005", "--kind", "depends_on")
    assert code == 1
    assert "no depends_on relation" in out


def test_relate_kind_choices_match_model() -> None:
    """CLI держит свою копию видов, чтобы не тянуть SQLAlchemy в старт (ADO-179)."""
    from cod_doc.cli.adr.cmd_relate import _KINDS
    from cod_doc.infra.models.adrs import ADR_RELATION_KINDS

    assert _KINDS == ADR_RELATION_KINDS


@pytest.mark.usefixtures("root")
def test_sync_clear_decided_at() -> None:
    assert _run("sync", "ADR-005", "-p", "p", "--decided-at", "2026-05-17")[0] == 0
    assert _show("ADR-005")["decided_at"] == "2026-05-17"

    code, out = _run("sync", "ADR-005", "-p", "p", "--clear-decided-at")
    assert code == 0, out
    assert _show("ADR-005")["decided_at"] is None

    code, out = _run(
        "sync", "ADR-005", "-p", "p", "--clear-decided-at", "--decided-at", "2026-01-01"
    )
    assert code == 2
    assert "mutually exclusive" in out
