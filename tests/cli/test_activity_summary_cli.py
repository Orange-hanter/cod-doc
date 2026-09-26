"""AFT-008 (RFC 27 F9): CLI `activity summary` — агрегаты журнала activity_event.

Один вызов отвечает на «события за 14 дней по дням и actor_kind»: GROUP BY
живёт в `activity_service.summarize` (покрыт tests/services/test_activity_summary.py),
здесь — что опции CLI доезжают до сервиса, ошибки приходят ненулевым кодом без
трейсбека, а вывод совпадает с литеральными эталонами. Запрещено строить
ожидание вызовом activity_service.summarize или сравнением с выводом MCP
activity_summary — эталоны ниже литералы.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from click.testing import CliRunner, Result
from sqlalchemy import delete

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import ActivityEventModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import activity_service

if TYPE_CHECKING:
    from pathlib import Path

_PROJECT = "actv"
_SINCE = "2026-09-12"


def _seed(tmp_path: Path) -> str:
    """Зарегистрированный проект с 6 событиями в окне 14 дней и 1 до --since.

    Внутри окна [2026-09-12 ..]: 3 дня × actor_kind human/agent/routine;
    событие 2026-09-01 обязано отсекаться --since. ts/actor_kind — литералы.
    """
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
            proj = project.row_id

            # Бутстрап `project add` пишет свои системные события «сегодня» и
            # засорял бы агрегат; канон сида обязан быть литеральным.
            session.execute(delete(ActivityEventModel).where(ActivityEventModel.project_id == proj))

            def _put(
                kind: str,
                *,
                actor_kind: str,
                actor_id: str,
                scope_kind: str,
                ts: datetime,
            ) -> None:
                activity_service.emit(
                    session,
                    proj,
                    kind,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    scope_kind=scope_kind,
                    ts=ts,
                )

            _put(
                "task.created",
                actor_kind="human",
                actor_id="human:dakh",
                scope_kind="task",
                ts=datetime(2026, 9, 12, 10, tzinfo=UTC),
            )
            _put(
                "doc.updated",
                actor_kind="human",
                actor_id="human:dakh",
                scope_kind="doc",
                ts=datetime(2026, 9, 12, 15, tzinfo=UTC),
            )
            _put(
                "task.created",
                actor_kind="agent",
                actor_id="agent:claude-opus-5",
                scope_kind="task",
                ts=datetime(2026, 9, 13, 9, tzinfo=UTC),
            )
            _put(
                "task.completed",
                actor_kind="agent",
                actor_id="agent:claude-opus-5",
                scope_kind="task",
                ts=datetime(2026, 9, 13, 12, tzinfo=UTC),
            )
            _put(
                "doc.updated",
                actor_kind="routine",
                actor_id="routine:doc_drift_daily",
                scope_kind="doc",
                ts=datetime(2026, 9, 14, 8, tzinfo=UTC),
            )
            _put(
                "task.created",
                actor_kind="agent",
                actor_id="agent:claude-opus-5",
                scope_kind="task",
                ts=datetime(2026, 9, 14, 9, tzinfo=UTC),
            )
            # Вне окна: до --since.
            _put(
                "doc.updated",
                actor_kind="human",
                actor_id="human:dakh",
                scope_kind="doc",
                ts=datetime(2026, 9, 1, 10, tzinfo=UTC),
            )
    finally:
        engine.dispose()
    return _PROJECT


def _run(args: list[str]) -> Result:
    # FORCE_COLOR из окружения CI не должен добавлять ANSI в вывод.
    return CliRunner().invoke(
        main,
        ["activity", "summary", "-p", _PROJECT, *args],
        env={"FORCE_COLOR": ""},
    )


def test_summary_14_days_by_day_and_actor_kind(tmp_path: Path) -> None:
    """«События за 14 дней по дням и actor_kind» — одна команда (критерий 1)."""
    _seed(tmp_path)
    result = _run(["--since", _SINCE, "--group-by", "day", "--group-by", "actor_kind", "--json"])
    assert result.exit_code == 0, result.output

    assert json.loads(result.output) == [
        {"day": "2026-09-12", "actor_kind": "human", "n": 2},
        {"day": "2026-09-13", "actor_kind": "agent", "n": 2},
        {"day": "2026-09-14", "actor_kind": "agent", "n": 1},
        {"day": "2026-09-14", "actor_kind": "routine", "n": 1},
    ]


def test_summary_default_group_by_day(tmp_path: Path) -> None:
    """Без --group-by агрегация идёт по дням: ключи строк == {'day', 'n'}."""
    _seed(tmp_path)
    result = _run(["--since", _SINCE, "--json"])
    assert result.exit_code == 0, result.output

    rows = json.loads(result.output)
    assert rows == [
        {"day": "2026-09-12", "n": 2},
        {"day": "2026-09-13", "n": 2},
        {"day": "2026-09-14", "n": 2},
    ]
    for row in rows:
        assert set(row) == {"day", "n"}


def test_summary_bad_since_is_usage_error(tmp_path: Path) -> None:
    """Непарсящийся --since — ненулевой код, без трейсбека."""
    _seed(tmp_path)
    result = _run(["--since", "yesterday"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_summary_invalid_group_by_rejected(tmp_path: Path) -> None:
    """--group-by week отклоняется click.Choice с перечнем допустимых значений."""
    _seed(tmp_path)
    result = _run(["--since", _SINCE, "--group-by", "week"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    for allowed in ("day", "actor_kind", "kind", "scope_kind"):
        assert allowed in result.output


def test_summary_table_mode(tmp_path: Path) -> None:
    """Без --json — rich-таблица с литеральными actor_kind из сида."""
    _seed(tmp_path)
    result = _run(["--since", _SINCE, "--group-by", "day", "--group-by", "actor_kind"])
    assert result.exit_code == 0, result.output
    for actor_kind in ("human", "agent", "routine"):
        assert actor_kind in result.output
