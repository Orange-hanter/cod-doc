"""ACU-010: анти-drift — мутация в ``approval_service`` обязана быть на MCP и CLI.

До ACU-010 у одобрений была только MCP-поверхность (RFC 28 S7): человек,
которому куратор оставляет предложения ``doc_patch``, не мог их разобрать из
терминала. Группа ``cod-doc approval`` закрыла ``resolve`` и ``cancel``.

Механика — общая, ``tests/services/_surface_parity.py``.
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
    name="approval_service",
    known_mutations=frozenset({"request", "request_doc_patch", "resolve", "cancel"}),
    known_reads=frozenset({"get", "list_approvals", "to_dict", "doc_patch_fingerprint"}),
    internal_only={
        "request_doc_patch": (
            "предложение doc_patch собирает только фоновый куратор (RFC 28 §3.6) — из "
            "отпечатка, головы ревизий и diff; ни человеку в CLI, ни агенту на MCP "
            "собирать его руками незачем, а разбирают его через resolve"
        ),
    },
    surface_debt={
        "request": (
            frozenset({"cli"}),
            "запросить одобрение — действие агента, который ждёт решения (MCP "
            "approval_request); человек в терминале решения принимает, а не "
            "запрашивает у самого себя",
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
    """Новая write-функция в approval_service роняет тест, пока её не впишут в спеку."""
    assert _surface_parity.discovered_mutations(SPEC) == {
        "request",
        "request_doc_patch",
        "resolve",
        "cancel",
    }


def test_leaky_surface_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка не зеленеет на дырявой поверхности: CLI без ``resolve``."""
    original = _surface_parity.surface_members

    def leaky(surface: str, spec: ServiceSpec) -> set[str]:
        members = original(surface, spec)
        if surface == "cli":
            members.discard("resolve")
        return members

    monkeypatch.setattr(_surface_parity, "surface_members", leaky)
    with pytest.raises(AssertionError, match="resolve"):
        check_every_mutation_is_exposed(SPEC)
