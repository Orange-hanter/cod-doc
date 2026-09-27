"""ADO-209: анти-drift — мутация в ``plan_service`` обязана быть на MCP и CLI.

RFC 26 §5.2, задача 6. До RFC 26 ``plan_service`` под сканером не стоял, и
покрывать было нечего: write-функций в пакете не было вовсе — секции и
планы создавались мимо сервиса. RFC 26 завёл пять мутаций
(``create_plan``, ``create_section``, ``update_section``, ``move_section``,
``delete_section``) и сразу выставил их на обе поверхности; этот тест
держит паритет дальше.

Механика — общая с ``test_task_mutation_surface_parity.py`` и
``test_doc_mutation_surface_parity.py``, живёт в
``tests/services/_surface_parity.py``: мутации обнаруживаются из AST по
записи в audit-trail, а не по ручному списку.

Ловушка — ``freeze_projection`` (``plan_service/export.py``). Функция пишет,
но пишет через ``doc_service.create``: этого вызова нет в
``AUDIT_WRITE_CALLS``, и это не module-local хелпер, поэтому детектор её не
видит. В спеку она сознательно не внесена ни в каком качестве. В
``known_mutations`` — нельзя: детектор её не находит, смоук упал бы. В
``known_reads`` — тоже нет: там утверждение «детектор не должен считать это
мутацией» формально верное, но пустое, а для пишущей функции ещё и
вводящее в заблуждение. Запись ревизии документа — дело ``doc_service``, чей
паритет стережёт ``test_doc_mutation_surface_parity.py``.

``surface_debt`` и ``internal_only`` пусты: паритет полный.
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
    name="plan_service",
    known_mutations=frozenset(
        {
            "create_plan",
            "create_section",
            "update_section",
            "move_section",
            "delete_section",
        }
    ),
    known_reads=frozenset(
        {
            # reads.py
            "get_by_scope",
            "list_sections",
            "get_for_project",
            "list_for_project",
            "recalc_for_project",
            "plan_scopes",
            "require_plan_in_project",
            "section_progress_for_project",
            "progress_for_project",
            "list_plans_summary",
            "ready_batch_for_project",
            "ready_for_project",
            "recalc",
            "ready_batch",
            "ready",
            # audit.py, export.py (без freeze_projection — см. докстринг)
            "audit",
            "export",
            # graph.py
            "forward_chain",
            "reverse_chain",
            "chain_layout",
            "critical_path",
            # _internals.py
            "build_section",
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


def test_freeze_projection_outside_spec() -> None:
    """Детектор ``freeze_projection`` не видит, а спека её не упоминает."""
    assert "freeze_projection" not in SPEC.known_mutations
    assert "freeze_projection" not in SPEC.known_reads
    assert "freeze_projection" not in _surface_parity.discovered_mutations(SPEC)


def test_discovered_mutations_exact() -> None:
    """Новая write-функция в plan_service роняет тест, пока её не впишут в спеку.

    Эталон — литерал: взятый из ``SPEC.known_mutations``, он сравнивал бы
    спеку саму с собой через детектор и пропускал бы расширение спеки
    вместе с кодом без ревью этого файла.
    """
    assert _surface_parity.discovered_mutations(SPEC) == {
        "create_plan",
        "create_section",
        "update_section",
        "move_section",
        "delete_section",
    }


def test_leaky_surface_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка не зеленеет на дырявой поверхности: CLI без ``move_section``."""
    original = _surface_parity.surface_members

    def leaky(surface: str, spec: ServiceSpec) -> set[str]:
        members = original(surface, spec)
        if surface == "cli":
            members.discard("move_section")
        return members

    monkeypatch.setattr(_surface_parity, "surface_members", leaky)
    with pytest.raises(AssertionError, match="move_section"):
        check_every_mutation_is_exposed(SPEC)
