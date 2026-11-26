"""REL-010: анти-drift — мутация в ``relation_service`` обязана быть на MCP и CLI.

RFC 39 §3.7. Механика общая с остальными ``test_*_mutation_surface_parity``
(``_surface_parity.py``): мутации находятся из AST по записи в audit-trail.

``record`` пишет ревизию и событие, но это шаг внутри ``relate``/``unrelate`` и
подписчика ``EntityDeleted`` — отдельной операцией пользователя не является.
Подписчик ``cleanup.on_entity_deleted`` зовёт ``record`` импортом, а не
module-local хелпером, поэтому детектор его не видит и в спеку он не входит.
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
    name="relation_service",
    known_mutations=frozenset({"relate", "unrelate", "define_type", "deprecate_type"}),
    known_reads=frozenset({"relations", "relation_types", "verify", "load", "to_domain"}),
    internal_only={
        "record": "шаг audit-trail внутри relate/unrelate и подписчика EntityDeleted, не операция",
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
    """Новая write-функция роняет тест, пока её не впишут в спеку (эталон — литерал)."""
    assert _surface_parity.discovered_mutations(SPEC) == {
        "relate",
        "unrelate",
        "define_type",
        "deprecate_type",
        "record",
    }


def test_leaky_surface_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    original = _surface_parity.surface_members

    def leaky(surface: str, spec: ServiceSpec) -> set[str]:
        members = original(surface, spec)
        if surface == "cli":
            members.discard("define_type")
        return members

    monkeypatch.setattr(_surface_parity, "surface_members", leaky)
    with pytest.raises(AssertionError, match="define_type"):
        check_every_mutation_is_exposed(SPEC)
