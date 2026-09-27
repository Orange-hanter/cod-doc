"""ADO-204 (RFC 26 §5): CLI `plan create` поверх `plan_service.create_plan`.

Семантика сервиса покрыта сервисными тестами; здесь — что команда пишет план
с ревизией и событием, а отказы доносит сообщением без трейсбека. Эталоны —
литералы и прямые SELECT по plan, revision, activity_event.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result
from sqlalchemy import text

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "tpcc"
_SCOPE = "plan-x"


def _seed(tmp_path: Path) -> None:
    """Пустой проект в реестре под изолированным COD_DOC_HOME."""
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output


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


def _plans() -> list[tuple[object, ...]]:
    """(scope, slug проекта, principle) всех планов прямым SELECT."""
    return _query(
        "SELECT pl.scope, p.slug, pl.principle FROM plan pl "
        "JOIN project p ON p.row_id = pl.project_id ORDER BY pl.row_id"
    )


def _create(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(
        main, ["plan", "create", *args, "-p", _PROJECT], env={"FORCE_COLOR": ""}
    )


def test_plan_create_json(tmp_path: Path) -> None:
    """--json: три ключа; в БД план, ревизия plan и событие plan.created."""
    _seed(tmp_path)
    result = _create([_SCOPE, "--principle", "P", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) == {"plan_id", "scope", "principle"}
    assert data["scope"] == "plan-x"
    assert data["principle"] == "P"

    assert _plans() == [("plan-x", "tpcc", "P")]
    (plan_row_id,) = _query("SELECT row_id FROM plan WHERE scope = 'plan-x'")[0]
    assert data["plan_id"] == plan_row_id
    assert _query("SELECT entity_id, author FROM revision WHERE entity_kind = 'plan'") == [
        (plan_row_id, "cli")
    ]
    assert _query("SELECT count(*) FROM activity_event WHERE kind = 'plan.created'") == [(1,)]


def test_plan_create_duplicate_is_message(tmp_path: Path) -> None:
    """Повтор scope — exit 1 с текстом сервиса, без трейсбека; план один."""
    _seed(tmp_path)
    first = _create([_SCOPE, "--principle", "P"])
    assert first.exit_code == 0, first.output

    again = _create([_SCOPE, "--principle", "P"])
    assert again.exit_code == 1, again.output
    assert "Plan with scope 'plan-x' already exists." in again.output
    assert "Traceback" not in again.output
    assert _query("SELECT count(*) FROM plan") == [(1,)]
    assert _query("SELECT count(*) FROM revision WHERE entity_kind = 'plan'") == [(1,)]


def test_plan_create_requires_principle(tmp_path: Path) -> None:
    """Без --principle click отказывает до записи."""
    _seed(tmp_path)
    result = _create([_SCOPE])
    assert result.exit_code != 0
    assert _query("SELECT count(*) FROM plan") == [(0,)]


def test_plan_create_reason_recorded(tmp_path: Path) -> None:
    """--reason ложится в revision.reason."""
    _seed(tmp_path)
    result = _create([_SCOPE, "--principle", "P", "--reason", "seed"])
    assert result.exit_code == 0, result.output
    assert _query("SELECT reason FROM revision WHERE entity_kind = 'plan'") == [("seed",)]


def test_plan_create_human_mode(tmp_path: Path) -> None:
    """Без --json — строка со scope."""
    _seed(tmp_path)
    result = _create([_SCOPE, "--principle", "P"])
    assert result.exit_code == 0, result.output
    assert "plan-x" in result.output
    assert _plans() == [("plan-x", "tpcc", "P")]


def test_plan_create_unknown_project_is_message(tmp_path: Path) -> None:
    """Слаг не из реестра — exit 1 сообщением, без трейсбека."""
    _seed(tmp_path)
    result = CliRunner().invoke(
        main,
        ["plan", "create", _SCOPE, "-p", "nope", "--principle", "P"],
        env={"FORCE_COLOR": ""},
    )
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
