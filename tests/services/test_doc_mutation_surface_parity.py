"""STO-017: анти-drift — мутация в ``doc_service`` обязана быть на MCP и CLI.

ADO-067 завёл машинную проверку паритета только для ``task_service``. Для
``doc_service`` такой проверки не было — именно поэтому ``add_section`` и
``patch_section`` прожили в web-онли незамеченными с 2026-06 по 2026-09:
документ, созданный в БД, не мог получить ни одной секции иначе как через
файл на диске и ``doc import``. Закрыто в STO-010/STO-011; этот тест
существует, чтобы следующая такая функция не прожила так же долго.

Механика — общая с ``test_task_mutation_surface_parity.py``, живёт в
``tests/services/_surface_parity.py``: мутации обнаруживаются из AST по
записи в audit-trail, а не по ручному списку.

Что остаётся незакрытым после STO-010/011 — в ``surface_debt`` ниже, с
обоснованием на каждую запись. Список ratchet: тест падает, когда пробел
закрыт, а запись из списка не убрана.
"""

from __future__ import annotations

import pytest

from ._surface_parity import (
    ServiceSpec,
    check_allowlists_carry_a_justification,
    check_discovery_finds_known_mutations,
    check_every_mutation_is_exposed,
    check_spec_resolves_to_real_sources,
    check_surface_debt_ratchet_is_current,
)

SPEC = ServiceSpec(
    name="doc_service",
    known_mutations=frozenset(
        {
            "create",
            "add_section",
            "patch_section",
            "update_status",
            "accept",
            "rename",
            "delete",
        }
    ),
    # Включая чистые хелперы формата (``section_diff`` и соседи): они
    # появились вместе с write-тулами STO-010/011 и не должны попадать в
    # мутации, иначе детектор начнёт требовать под них тул и команду.
    known_reads=frozenset(
        {
            "get",
            "get_by_path",
            "get_sections",
            "get_section_by_id",
            "get_doc_by_id",
            "list_for_project",
            "list_delete_candidates",
            "render_body",
            "content_hash",
            "section_diff",
            "section_create_diff",
            "section_label",
        }
    ),
    surface_debt={
        "update_status": (
            frozenset({"mcp", "cli"}),
            "Из семи переходов жизненного цикла наружу выставлен ровно один — "
            "DRAFT/REVIEW → ACTIVE через `accept` (doc_accept / doc accept), и он "
            "есть на обеих поверхностях. Произвольная смена статуса, в первую "
            "очередь понижение до DEPRECATED, пока только в web: это решение о "
            "судьбе документа, а не правка текста. Выставлять — отдельной "
            "задачей вместе с правилами перехода, не попутно с STO-010/011.",
        ),
        "delete": (
            frozenset({"mcp"}),
            "CLI `doc delete` есть (ADO-031), MCP-тула нет намеренно: удаление "
            "документа с агентской поверхности — отдельное решение по "
            "безопасности. Человек в терминале подтверждает удаление и видит "
            "список кандидатов; агенту тот же вызов доступен без подтверждения.",
        ),
    },
)

SPECS = (SPEC,)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_discovery_finds_the_known_mutations(spec: ServiceSpec) -> None:
    check_discovery_finds_known_mutations(spec)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_every_mutation_is_exposed_on_mcp_and_cli(spec: ServiceSpec) -> None:
    check_every_mutation_is_exposed(spec)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_surface_debt_ratchet_is_current(spec: ServiceSpec) -> None:
    check_surface_debt_ratchet_is_current(spec)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_allowlists_carry_a_justification(spec: ServiceSpec) -> None:
    check_allowlists_carry_a_justification(spec)


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.name)
def test_every_spec_resolves_to_real_sources(spec: ServiceSpec) -> None:
    check_spec_resolves_to_real_sources(spec)


def test_section_writes_are_on_both_surfaces() -> None:
    """Регрессия на сам пробел STO-010/011, а не только на механику.

    Если ``doc_add_section`` / ``doc_patch_section`` или их CLI-зеркала
    исчезнут, общий тест тоже упадёт — но его сообщение будет про abstract
    «мутацию без поверхности». Этот кейс называет пропажу своим именем.
    """
    from ._surface_parity import surface_members

    for surface in ("mcp", "cli"):
        members = surface_members(surface, SPEC)
        missing = sorted({"add_section", "patch_section"} - members)
        assert not missing, (
            f"правка секций пропала с поверхности {surface}: {missing}. "
            "Цикл doc_create → add_section → patch_section обязан проходиться "
            "и агентом, и человеком (STO-010 / STO-011)."
        )
