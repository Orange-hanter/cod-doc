"""ADO-044 / ADR-012: единственная точка вывода ``actor_kind`` из ``author``.

До ADR-012 эвристика ``author.startswith("agent")`` была продублирована
в одиннадцати местах в трёх несовместимых вариантах, из-за чего один и
тот же оркестраторный прогон попадал в ``activity_event`` то как
``orchestrator``, то как ``human`` (замер на живой БД 2026-09-06:
58 событий ``orchestrator-run-kimi-sprint-20260828`` с
``actor_kind='human'`` против 27 с ``actor_kind='orchestrator'``).
"""

from __future__ import annotations

import pytest

from cod_doc.domain.entities import ActorKind, actor_kind_for_author
from cod_doc.services import activity_service


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        # Канонический формат <kind>:<id>.
        ("human:dakh", ActorKind.HUMAN),
        ("human:cli", ActorKind.HUMAN),
        ("agent:claude-opus-5", ActorKind.AGENT),
        ("agent:claude", ActorKind.AGENT),
        ("orchestrator:kimi", ActorKind.ORCHESTRATOR),
        ("routine:doc_drift_daily", ActorKind.ROUTINE),
        # Дефисная форма оркестраторного прогона — та, что реально в БД.
        ("orchestrator-run-kimi-sprint-20260828", ActorKind.ORCHESTRATOR),
        ("agent-task-steward", ActorKind.AGENT),
        # Голые формы.
        ("agent", ActorKind.AGENT),
        ("mcp", ActorKind.SYSTEM),
        ("mcp:claude", ActorKind.SYSTEM),
        ("system", ActorKind.SYSTEM),
        # Legacy-хвост без префикса — за ним стоит человек.
        ("cli", ActorKind.HUMAN),
        ("claude-opus-5", ActorKind.HUMAN),
        ("roadmap-sync", ActorKind.HUMAN),
        ("kimi-m4", ActorKind.HUMAN),
        ("", ActorKind.HUMAN),
        (None, ActorKind.HUMAN),
    ],
)
def test_actor_kind_for_author(author: str | None, expected: ActorKind) -> None:
    assert actor_kind_for_author(author) is expected


def test_orchestrator_run_is_not_classified_as_human() -> None:
    """Регресс ADR-012: `"run" in agent` и `startswith("agent")` давали `human`."""
    assert actor_kind_for_author("orchestrator-run-swarm-20260826") == "orchestrator"


def test_result_is_a_plain_str_for_the_orm_column() -> None:
    """``activity_event.actor_kind`` — String(32); StrEnum должен писаться как строка."""
    value = actor_kind_for_author("agent:claude")
    assert isinstance(value, str)
    assert value == "agent"


def test_activity_service_reexport_delegates_to_domain() -> None:
    """Обёртка в services/ осталась, но собственной эвристики в ней нет."""
    for author in ("orchestrator-run-x", "agent:x", "human:x", "mcp", "cli"):
        assert activity_service._actor_kind_for_author(author) == actor_kind_for_author(author)


def test_every_returned_kind_is_in_the_canonical_vocabulary() -> None:
    """Резолвер не изобретает значений вне ActorKind."""
    samples = [
        "human:dakh",
        "agent:claude",
        "orchestrator-run-x",
        "routine:daily",
        "mcp",
        "whatever-legacy",
    ]
    assert {actor_kind_for_author(a) for a in samples} <= set(ActorKind)
