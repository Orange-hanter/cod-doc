"""ADO-067: анти-drift — мутация в task/story-сервисах обязана быть на MCP и CLI.

Как ADO-067 просочился мимо ревью: ``update_description`` и
``update_acceptance`` жили в ``task_service`` с 2026-06, но были подключены
ТОЛЬКО к web-фрагментам (``api/web/fragments/tasks_fields.py``). Ни один
тест этого не замечал — ``tests/services/test_activity_write_path.py``
перечисляет сервисы **вручную**, поэтому не видит ни новых write-сервисов,
ни неподключённых старых.

Отсюда конструкция проверки: обнаружение мутаций из AST вместо ручного
списка. Механика живёт в ``tests/services/_surface_parity.py`` — там же
описаны метод, его границы и почему «пишет audit-trail» ≡ «мутация».

ADO-159: проверка распространена со ``task_service.py`` на пакет
``story_service/`` — семь мутаций вместо только задачных. До этого
«story-мутации есть и в MCP, и в CLI» держалось на примерах.

STO-017: сканер вынесен в общий хелпер и переиспользован
``test_doc_mutation_surface_parity.py``. Причина та же, что и у ADO-067,
только с другой стороны: ``doc_service.add_section``/``patch_section``
прожили в web-онли ровно потому, что машинная проверка покрывала только
task_service.

Имя файла осталось прежним: на него ссылаются CLAUDE.md и отчёт аудита
``2026-09-06-sprint-m5-trustworthy-gate``, а переписывать исторический
отчёт ради имени файла — хуже, чем потерпеть узкое имя.

Сервисы здесь — ``task_service.py`` и пакет ``story_service/``. Мутации в
соседних сервисах (checkout/agent) имеют свои протоколы и свои тесты.

Разрешённые исключения живут в явных списках на каждый сервис; на каждую
запись — обоснование. ``surface_debt`` — ratchet: тест сам падает, когда
запись устарела (функция выставлена, а из списка не убрана), поэтому список
может только сокращаться.
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

SPECS = (
    ServiceSpec(
        name="task_service",
        known_mutations=frozenset(
            {
                "create",
                "update_status",
                "update_description",
                "update_acceptance",
                "update_priority",
                "complete",
            }
        ),
        known_reads=frozenset({"get", "list_for_project"}),
        surface_debt={
            "log_progress": (
                frozenset({"cli"}),
                "Лог прогресса — часть heartbeat-протокола агента (MCP task_log_progress). "
                "Человеку из терминала он не нужен, поэтому CLI-команды нет; "
                "выставлять — отдельным решением, не в ADO-067.",
            ),
            "set_blocker": (
                frozenset({"cli"}),
                "Внешний блокер ставится агентом по ходу работы (MCP task_set_blocker). "
                "Пробел в CLI существует с PCA-эпохи и не входит в скоуп ADO-067 "
                "(description/acceptance/priority).",
            ),
            "clear_blocker": (
                frozenset({"cli"}),
                "Парная к set_blocker; снимается там же, где ставилась. Тот же пробел "
                "в CLI, то же обоснование.",
            ),
        },
    ),
    ServiceSpec(
        name="story_service",
        known_mutations=frozenset(
            {
                "create",
                "update_status",
                "create_section",
                "assign_section",
                "add_criterion",
                "set_criterion_met",
            }
        ),
        known_reads=frozenset({"get", "list_sections", "list_acceptance", "coverage"}),
    ),
)

SPEC_BY_NAME = {spec.name: spec for spec in SPECS}


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
