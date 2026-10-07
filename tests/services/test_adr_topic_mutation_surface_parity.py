"""ARG-008: анти-drift — мутация в ``adr_topic_service`` обязана быть на MCP и CLI.

Сервис полок завёден сразу под сканер (``tests/services/_surface_parity.py``):
все четыре мутации выставлены на обе поверхности, ``surface_debt`` пуст.
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

SPEC = ServiceSpec(
    name="adr_topic_service",
    known_mutations=frozenset({"create", "update", "move", "delete"}),
    known_reads=frozenset({"list_for_project", "get", "require", "adr_counts", "topic_to_dict"}),
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
    """Новая write-функция в adr_topic_service роняет тест, пока её не впишут в спеку."""
    assert _surface_parity.discovered_mutations(SPEC) == {"create", "update", "move", "delete"}


def test_leaky_surface_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка не зеленеет на дырявой поверхности: MCP без ``move``."""
    original = _surface_parity.surface_members

    def leaky(surface: str, spec: ServiceSpec) -> set[str]:
        members = original(surface, spec)
        if surface == "mcp":
            members.discard("move")
        return members

    monkeypatch.setattr(_surface_parity, "surface_members", leaky)
    with pytest.raises(AssertionError, match="move"):
        check_every_mutation_is_exposed(SPEC)
