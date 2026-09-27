"""OQM-003: анти-drift — мутация в ``question_service`` обязана быть на MCP и CLI.

Механика — общая с ``test_plan_mutation_surface_parity.py``, живёт в
``tests/services/_surface_parity.py``: мутации обнаруживаются из AST по
записи в audit-trail, а не по ручному списку.

``verify_links`` пишет (``resolved`` / ``broken_reason`` / ``last_checked``
на рёбрах), но это производное состояние, как ``link.resolved``: ни ревизии,
ни события оно не оставляет, поэтому детектор его не видит и в спеке оно
числится чтением. На обеих поверхностях оно всё равно есть —
``question_verify`` и ``cod-doc question verify``.

``surface_debt`` и ``internal_only`` пусты: паритет полный.
"""

from __future__ import annotations

from . import _surface_parity
from ._surface_parity import (
    ServiceSpec,
    check_allowlists_carry_a_justification,
    check_discovery_finds_known_mutations,
    check_every_mutation_is_exposed,
    check_spec_resolves_to_real_sources,
    check_surface_debt_ratchet_is_current,
)

MUTATIONS = frozenset(
    {
        "create",
        "update",
        "resolve",
        "drop",
        "reopen",
        "add_option",
        "update_option",
        "remove_option",
        "link",
        "unlink",
    }
)

SPEC = ServiceSpec(
    name="question_service",
    known_mutations=MUTATIONS,
    known_reads=frozenset(
        {
            "get",
            "list_for_project",
            "list_links",
            "list_options",
            "questions_for_targets",
            "check_edge",
            "verify_links",
            "broken_links",
            "question_to_dict",
            "question_summary",
            "option_to_dict",
            "link_to_dict",
            "validate_ref",
            "validate_question_id",
            "validate_text",
            "split_code_ref",
        }
    ),
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
    """Новая write-функция в question_service роняет тест, пока её не впишут в спеку."""
    assert _surface_parity.discovered_mutations(SPEC) == {
        "create",
        "update",
        "resolve",
        "drop",
        "reopen",
        "add_option",
        "update_option",
        "remove_option",
        "link",
        "unlink",
    }
