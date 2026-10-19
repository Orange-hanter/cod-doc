"""CLO-016: анти-drift — мутация в ``sync_service`` обязана быть на MCP и CLI.

RFC 29 §3.9, §10.6: гейты паритета расширяются на ``sync_service``. Механика
— общая, ``tests/services/_surface_parity.py``: мутации находятся из AST по
записи в audit-trail. В пакете их две — ``push`` (событие ``sync.pushed``
пишет module-local ``_commit``) и ``pull`` (``sync.pulled``); обе выставлены
как ``push``/``pull`` в CLI и ``sync_push``/``sync_pull`` в MCP.

Ловушка детектора поверхностей: модули пакета называются так же, как их
функции (``sync_service.pull.pull``). Поверхности импортируют модуль
(``from cod_doc.services.sync_service import pull as pull_mod``), и сканер
засчитывает имя ``pull`` по импорту модуля. Зовут при этом именно функцию —
``test_surfaces_call_the_functions`` сверяет это отдельно.

Вне детектора, по построению: ``fetch``, ``status``, ``log``, ``verify``
только читают (``fetch`` пишет одну local-строку ``sync_ref`` без события);
``remotes.add``/``remove`` пишут ``config.yaml``, а не БД, и выставлены в CLI
— в MCP их нет намеренно (§10.7). ``resolve_conflict`` выставлен как
``sync resolve`` в CLI и ``sync_resolve`` в MCP (CLO-026); ``open_conflict``
— внутренний: конфликт открывает слияние внутри pull. ``close`` — тоже
внутренний: автозакрытие конфликтов цепочкой статуса (CLO-044) и маркером
разрешения с другой реплики (CLO-045) идёт внутри pull. ``reconcile`` —
шаг push/pull: провизорный замок checkout (``--offline``) получает аренду
(CLO-031); сам checkout выставлен как ``task checkout``/``task_checkout``.
"""

from __future__ import annotations

import pytest

from . import _surface_parity
from ._surface_parity import (
    SURFACE_DIRS,
    ServiceSpec,
    check_allowlists_carry_a_justification,
    check_discovery_finds_known_mutations,
    check_every_mutation_is_exposed,
    check_spec_resolves_to_real_sources,
    check_surface_debt_ratchet_is_current,
)

SPEC = ServiceSpec(
    name="sync_service",
    known_mutations=frozenset(
        {
            "push",
            "pull",
            "open_conflict",
            "resolve_conflict",
            "resolve_key",
            "apply_changeset",
            "close",
            "pin_provisional",
            "reconcile",
            "reconcile_quietly",
            "clone",
        }
    ),
    known_reads=frozenset(
        {
            # pull.py, status.py, digest.py
            "fetch",
            "status",
            "log",
            "verify",
            "compute",
            "pending_count",
            # remotes.py — config.yaml, не БД
            "add",
            "remove",
            "list_for",
            "get",
            # changeset.py
            "decode",
            "encode",
            "sha",
            # apply.py
            "check_clock",
            "entity_by_uid",
            "orm_values",
            # conflicts.py
            "list_conflicts",
            "count_open",
        }
    ),
    surface_debt={
        "clone": (
            frozenset({"mcp"}),
            "RFC 29 §10.7: clone в MCP нет намеренно — он заводит проект в реестре машины, "
            "а MCP-демон общий для всех харнессов; это дело человека, как remote add",
        ),
    },
    internal_only={
        "reconcile": (
            "сверка провизорных замков checkout с арендой на remote (CLO-031) — шаг "
            "push/pull, а не отдельное действие; событие task.checkout_leased в её транзакции"
        ),
        "reconcile_quietly": (
            "обёртка reconcile для push/pull (CLO-031): сбой сети не отменяет обмен; "
            "сама по себе выставлена как push/pull и task_checkout"
        ),
        "pin_provisional": (
            "закрепление провизорных ID (CLO-030) — первый шаг push, а не отдельное "
            "действие; событие sync.alias_created пишется в его транзакции"
        ),
        "open_conflict": (
            "конфликт открывает слияние внутри pull (merge), а не пользователь; "
            "событие sync.conflict_opened пишется в транзакции применения changeset'а"
        ),
        "resolve_key": (
            "стратегия склейки ключей (CLO-023) зовётся из apply внутри pull; "
            "событие sync.alias_created/relettered пишется в транзакции changeset'а"
        ),
        "apply_changeset": (
            "применение changeset'а зовёт только pull; событие sync.conflict_resolved "
            "(take=deleted, CLO-027) пишется в его транзакции, когда удаление сносит "
            "строку с открытым конфликтом"
        ),
        "close": (
            "автозакрытие конфликтов без записи в сущность (_conflict_rows.close, "
            "markers.close): цепочка статуса без отказов (CLO-044) и маркер разрешения "
            "другой реплики (CLO-045) внутри pull; событие sync.conflict_resolved "
            "пишется в транзакции changeset'а"
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
    """Новая write-функция в sync_service роняет тест, пока её не впишут в спеку."""
    assert _surface_parity.discovered_mutations(SPEC) == {
        "push",
        "pull",
        "open_conflict",
        "resolve_conflict",
        "resolve_key",
        "apply_changeset",
        "close",
        "pin_provisional",
        "reconcile",
        "reconcile_quietly",
        "clone",
    }


@pytest.mark.parametrize("surface", sorted(SURFACE_DIRS))
@pytest.mark.parametrize(
    ("module", "call"), [("push", "push_mod.push("), ("pull", "pull_mod.pull(")]
)
def test_surfaces_call_the_functions(surface: str, module: str, call: str) -> None:
    """Имя засчитано по импорту модуля — проверяем, что зовут саму функцию."""
    sources = [
        p.read_text(encoding="utf-8")
        for p in SURFACE_DIRS[surface].rglob("*.py")
        if "__pycache__" not in p.parts
    ]
    importing = [
        src for src in sources if f"from cod_doc.services.sync_service import {module} as" in src
    ]
    assert importing, f"{surface}: модуль sync_service.{module} не импортируется"
    assert any(call in src for src in importing), f"{surface}: нет вызова {call}…)"


def test_leaky_surface_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка не зеленеет на дырявой поверхности: MCP без ``push``."""
    original = _surface_parity.surface_members

    def leaky(surface: str, spec: ServiceSpec) -> set[str]:
        members = original(surface, spec)
        if surface == "mcp":
            members.discard("push")
        return members

    monkeypatch.setattr(_surface_parity, "surface_members", leaky)
    with pytest.raises(AssertionError, match="push"):
        check_every_mutation_is_exposed(SPEC)
