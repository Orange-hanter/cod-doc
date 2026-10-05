"""ARG-001: анти-drift — мутация в ``adr_service`` обязана быть на MCP и CLI.

До ARG-001 ``adr_service`` под сканером не стоял, и три мутации жили только
на MCP (и в вебе): ``update``, ``add_diagram``, ``link_task``. Тест заводит
сервис под общую механику ``tests/services/_surface_parity.py`` и фиксирует
этот пробел в ``surface_debt`` — ratchet, он может только сокращаться.
Новые мутации (``relate``, ``unrelate``) выставлены на обе поверхности
сразу.
"""

from __future__ import annotations

import pytest

from . import _surface_parity
from ._surface_parity import (
    ServiceSpec,
    check_allowlists_carry_a_justification,
    check_discovery_finds_known_mutations,
    check_every_mutation_is_exposed,
    check_spec_resolves_to_real_sources,
    check_surface_debt_ratchet_is_current,
)

_CLI = frozenset({"cli"})

SPEC = ServiceSpec(
    name="adr_service",
    known_mutations=frozenset(
        {
            "create",
            "update",
            "sync_body",
            "deprecate",
            "add_diagram",
            "supersede",
            "link_task",
            "relate",
            "unrelate",
            "set_topic",
        }
    ),
    known_reads=frozenset(
        {
            "get",
            "list_for_project",
            "graph",
            "relations",
            "topic_name",
            "backlinks",
            "adr_to_dict",
            "next_adr_id",
            "render_markdown",
            "export_to_disk",
        }
    ),
    surface_debt={
        "update": (
            _CLI,
            "CLI не умеет ни принять, ни отклонить черновик: `adr update` не заведён "
            "со времён ADR-003, принятие идёт через MCP adr_update или веб",
        ),
        "add_diagram": (
            _CLI,
            "диаграмма прикрепляется с карточки в вебе или MCP adr_add_diagram; "
            "команды CLI для mermaid-тела не заводили",
        ),
        "link_task": (
            _CLI,
            "связь ADR ↔ задача ставится MCP adr_link_task; в CLI команды нет, "
            "просьб о ней в friction-логе не было",
        ),
    },
)


def test_spec_resolves_to_real_sources() -> None:
    check_spec_resolves_to_real_sources(SPEC)


def test_discovery_finds_the_known_mutations() -> None:
    check_discovery_finds_known_mutations(SPEC)


def test_every_mutation_is_exposed_on_mcp_and_cli() -> None:
    check_every_mutation_is_exposed(SPEC)


def test_surface_debt_ratchet_is_current() -> None:
    check_surface_debt_ratchet_is_current(SPEC)


def test_allowlists_carry_a_justification() -> None:
    check_allowlists_carry_a_justification(SPEC)


def test_discovered_mutations_exact() -> None:
    """Новая write-функция в adr_service роняет тест, пока её не впишут в спеку."""
    assert _surface_parity.discovered_mutations(SPEC) == {
        "create",
        "update",
        "sync_body",
        "deprecate",
        "add_diagram",
        "supersede",
        "link_task",
        "relate",
        "unrelate",
        "set_topic",
    }


def test_leaky_surface_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка не зеленеет на дырявой поверхности: CLI без ``relate``."""
    original = _surface_parity.surface_members

    def leaky(surface: str, spec: ServiceSpec) -> set[str]:
        members = original(surface, spec)
        if surface == "cli":
            members.discard("relate")
        return members

    monkeypatch.setattr(_surface_parity, "surface_members", leaky)
    with pytest.raises(AssertionError, match="relate"):
        check_every_mutation_is_exposed(SPEC)
