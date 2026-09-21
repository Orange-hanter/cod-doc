"""ADO-156: словарь статусов, который веб-форма предлагает человеку.

Форма — такая же поверхность записи, как CLI и MCP: пока `<select>` отдавал
`[s.value for s in TaskStatus]`, человек мог руками выбрать легаси-написание
(`pending`, `in-progress`) и вернуть в БД ровно тот дрейф, который вычищает
бэкфилл. Здесь стережём обе половины правила: канон предлагаем, легаси —
только показываем, если задача уже в нём лежит.
"""

from __future__ import annotations

from cod_doc.api.web.templates_env import (
    TASK_STATUS_OPTIONS,
    chain_done_count,
    task_status_choices,
)
from cod_doc.domain.entities import TASK_STATUS_ALIASES, TaskStatus

CANONICAL_COUNT = 7


def test_options_are_exactly_the_canonical_statuses() -> None:
    """Анти-drift: список = enum минус алиасы. Новый статус — правь обе стороны."""
    assert set(TASK_STATUS_OPTIONS) == {s.value for s in TaskStatus} - set(TASK_STATUS_ALIASES)
    assert len(TASK_STATUS_OPTIONS) == CANONICAL_COUNT


def test_options_carry_no_legacy_spelling() -> None:
    assert "pending" not in TASK_STATUS_OPTIONS
    assert "in-progress" not in TASK_STATUS_OPTIONS


def test_options_are_ordered_by_lifecycle_not_by_enum() -> None:
    """`done` не должен уезжать в хвост за `cancelled`-соседями из группы легаси."""
    assert TASK_STATUS_OPTIONS[:3] == ["backlog", "todo", "in_progress"]
    assert TASK_STATUS_OPTIONS.index("done") < TASK_STATUS_OPTIONS.index("cancelled")


def test_choices_default_to_canonical_only() -> None:
    assert [value for value, _label in task_status_choices()] == TASK_STATUS_OPTIONS
    assert [value for value, _label in task_status_choices("done")] == TASK_STATUS_OPTIONS


def test_choices_keep_a_legacy_current_value() -> None:
    """Не мигрированная БД (или восстановленный бэкап) не теряет выбранный пункт."""
    choices = task_status_choices("pending")
    assert ("pending", "pending (legacy)") in choices
    assert [value for value, _label in choices][:-1] == TASK_STATUS_OPTIONS


def test_choices_keep_an_unknown_current_value_unlabelled() -> None:
    """Чужая строка в колонке — не легаси-написание, врать про неё нечего."""
    assert ("weird", "weird") in task_status_choices("weird")


def test_chain_done_count_counts_closed_tasks() -> None:
    chain = {
        "levels": [
            {"level": 0, "tasks": [{"status": "done"}, {"status": "pending"}]},
            {"level": 1, "tasks": [{"status": "done"}, {"status": "blocked"}]},
        ]
    }
    assert chain_done_count(chain) == 2


def test_chain_done_count_on_empty_chain() -> None:
    assert chain_done_count({"levels": []}) == 0
    assert chain_done_count({}) == 0
