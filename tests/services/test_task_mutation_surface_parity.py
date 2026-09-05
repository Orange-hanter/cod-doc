"""ADO-067: анти-drift — мутация задачи в services обязана быть на MCP и CLI.

Закон репозитория (CLAUDE.md, «Четыре равные поверхности»): новая
функциональность в ``services/`` обязана появиться и в CLI, и в MCP —
агент и человек должны иметь тождественный интерфейс.

Как ADO-067 просочился мимо ревью: ``update_description`` и
``update_acceptance`` жили в ``task_service`` с 2026-06, но были подключены
ТОЛЬКО к web-фрагментам (``api/web/fragments/tasks_fields.py``). Ни один
тест этого не замечал — ``tests/services/test_activity_write_path.py``
перечисляет сервисы **вручную**, поэтому не видит ни новых write-сервисов,
ни неподключённых старых.

Отсюда конструкция этого теста: **обнаружение вместо ручного списка**.
Множество «мутаций задачи» вычисляется из AST ``task_service.py`` — публичная
функция считается мутацией, если её call-graph (с раскрытием module-local
хелперов вроде ``_update_text_field``) содержит запись в audit-trail:
``rev.write`` / ``activity_service.emit*`` /
``activity_service.write_revision_and_emit_event``. По правилу ADO-040
мутирующий сервис обязан писать revision и activity event, поэтому
«пишет audit-trail» ≡ «мутация» — новая функция попадает под проверку сама,
без правки теста.

Границы проверки:

* Модуль — только ``cod_doc/services/task_service.py``. Мутации задач в
  соседних сервисах (checkout/agent/story) имеют свои протоколы и свои
  тесты; расширять сюда — отдельная задача, не ADO-067.
* Поверхности — ``cod_doc/mcp/`` и ``cod_doc/cli/``, машинно-читаемые
  поверхности из контракта ADO-067. Web (``api/``) и TUI не проверяются:
  человеческие поверхности рендерят подмножество осознанно.

Разрешённые исключения живут в двух явных списках ниже; на каждую запись —
обоснование. ``SURFACE_DEBT`` — ratchet: тест сам падает, когда запись
устарела (функция выставлена, а из списка не убрана), поэтому список может
только сокращаться.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_SERVICE = REPO_ROOT / "cod_doc" / "services" / "task_service.py"
SURFACE_DIRS = {
    "mcp": REPO_ROOT / "cod_doc" / "mcp",
    "cli": REPO_ROOT / "cod_doc" / "cli",
}

TASK_SERVICE_MODULE = "cod_doc.services.task_service"
TASK_SERVICE_PACKAGE = "cod_doc.services"
TASK_SERVICE_NAME = "task_service"

# Вызовы, по которым функция опознаётся как мутация (ADO-040: write-path
# обязан оставить след). Хранятся как хвост dotted-имени вызова.
AUDIT_WRITE_CALLS = frozenset(
    {
        "rev.write",
        "revision_service.write",
        "activity_service.emit",
        "activity_service.emit_for_write",
        "activity_service.write_revision_and_emit_event",
    }
)

# --------------------------------------------------------------------------- #
# Разрешённые исключения                                                       #
# --------------------------------------------------------------------------- #

#: Функции, которые пишут audit-trail, но выставлять их наружу неправильно —
#: это внутренние шаги чужого протокола, а не операции пользователя.
#: Пусто на 2026-09-05; запись сюда — архитектурное решение, а не «пока не
#: успели».
INTERNAL_ONLY: dict[str, str] = {}

#: Ratchet: мутации, выставленные не на все поверхности ДО ADO-067. Значение —
#: (поверхности, где функции нет; причина). Список может только сокращаться:
#: ``test_surface_debt_ratchet_is_current`` падает, если функция уже
#: выставлена, а запись осталась.
SURFACE_DEBT: dict[str, tuple[frozenset[str], str]] = {
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
}


# --------------------------------------------------------------------------- #
# Обнаружение мутаций в task_service                                           #
# --------------------------------------------------------------------------- #


def _dotted(node: ast.expr) -> str:
    """Собрать dotted-имя из Name/Attribute; '' для всего остального."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return ""
    parts.append(cur.id)
    return ".".join(reversed(parts))


def _called_names(fn: ast.FunctionDef) -> set[str]:
    """Все dotted-имена, вызываемые внутри функции."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name:
                out.add(name)
    return out


def _task_service_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(TASK_SERVICE.read_text(encoding="utf-8"), filename=str(TASK_SERVICE))
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _writes_audit_trail(name: str, funcs: dict[str, ast.FunctionDef], seen: set[str]) -> bool:
    """True, если функция (или её module-local хелпер) пишет revision/event."""
    if name in seen:
        return False
    seen.add(name)
    calls = _called_names(funcs[name])
    if calls & AUDIT_WRITE_CALLS:
        return True
    return any(
        callee in funcs and _writes_audit_trail(callee, funcs, seen)
        for callee in calls
        if "." not in callee
    )


def discovered_mutations() -> dict[str, ast.FunctionDef]:
    """Публичные функции task_service, которые пишут audit-trail."""
    funcs = _task_service_functions()
    return {
        name: fn
        for name, fn in funcs.items()
        if not name.startswith("_") and _writes_audit_trail(name, funcs, set())
    }


# --------------------------------------------------------------------------- #
# Обнаружение использования на поверхностях                                    #
# --------------------------------------------------------------------------- #


def _task_service_members_used(py_file: Path) -> set[str]:
    """Имена членов task_service, к которым обращается файл поверхности.

    Учитывает три формы: ``from ...task_service import complete``,
    ``from cod_doc.services import task_service`` + ``task_service.create``
    и алиас (``... import task_service as tasks`` + ``tasks.update_priority``).
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    used: set[str] = set()
    aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == TASK_SERVICE_MODULE:
                used.update(a.name for a in node.names)
            elif node.module == TASK_SERVICE_PACKAGE:
                aliases.update(
                    a.asname or a.name for a in node.names if a.name == TASK_SERVICE_NAME
                )
        elif isinstance(node, ast.Import):
            aliases.update(a.asname or a.name for a in node.names if a.name == TASK_SERVICE_MODULE)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            head = _dotted(node)
            if not head:
                continue
            prefix, _, attr = head.rpartition(".")
            if prefix in aliases:
                used.add(attr)
    return used


def _surface_members(surface: str) -> set[str]:
    used: set[str] = set()
    for py_file in SURFACE_DIRS[surface].rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        used |= _task_service_members_used(py_file)
    return used


# --------------------------------------------------------------------------- #
# Тесты                                                                        #
# --------------------------------------------------------------------------- #


def test_discovery_finds_the_known_mutations() -> None:
    """Смоук на сам детектор: без него тест мог бы «зеленеть» на пустом множестве."""
    found = set(discovered_mutations())
    expected_subset = {
        "create",
        "update_status",
        "update_description",
        "update_acceptance",
        "update_priority",
        "complete",
    }
    assert expected_subset <= found, (
        "детектор мутаций сломан: не видит известные write-функции "
        f"{sorted(expected_subset - found)}. Проверь AUDIT_WRITE_CALLS."
    )
    assert "get" not in found, "детектор считает мутацией read-функцию get()"
    assert "list_for_project" not in found, "детектор считает мутацией list_for_project()"


def test_every_task_mutation_is_exposed_on_mcp_and_cli() -> None:
    """Мутация задачи обязана быть вызвана из cod_doc/mcp/ и из cod_doc/cli/."""
    mutations = set(discovered_mutations()) - set(INTERNAL_ONLY)
    surfaces = {name: _surface_members(name) for name in SURFACE_DIRS}

    missing: dict[str, list[str]] = {}
    for fn in sorted(mutations):
        gaps = sorted(s for s, members in surfaces.items() if fn not in members)
        allowed = SURFACE_DEBT.get(fn, (frozenset(), ""))[0]
        unexplained = [s for s in gaps if s not in allowed]
        if unexplained:
            missing[fn] = unexplained

    assert not missing, (
        "мутации task_service не выставлены на все поверхности: "
        f"{missing}. Закон CLAUDE.md «Четыре равные поверхности»: добавь тул в "
        "cod_doc/mcp/tools/task_tools.py и команду в cod_doc/cli/task.py. "
        "Если функция принципиально внутренняя — внеси её в INTERNAL_ONLY "
        "с обоснованием (одна строка на запись)."
    )


def test_surface_debt_ratchet_is_current() -> None:
    """SURFACE_DEBT может только сокращаться: закрытый пробел обязан уйти из списка."""
    surfaces = {name: _surface_members(name) for name in SURFACE_DIRS}
    stale: dict[str, list[str]] = {}
    for fn, (gaps, _reason) in SURFACE_DEBT.items():
        fixed = sorted(s for s in gaps if fn in surfaces[s])
        if fixed:
            stale[fn] = fixed
    assert not stale, (
        f"SURFACE_DEBT протух — эти функции уже выставлены: {stale}. "
        "Убери запись (или поверхность из неё): список ratchet, он только сокращается."
    )


def test_allowlists_carry_a_justification() -> None:
    """Пустое обоснование превращает allowlist в шум — запрещено (риск из контракта ADO-067)."""
    blank = [name for name, reason in INTERNAL_ONLY.items() if len(reason.strip()) < 20]
    blank += [name for name, (_g, reason) in SURFACE_DEBT.items() if len(reason.strip()) < 20]
    assert not blank, f"записи allowlist без внятного обоснования: {blank}"

    known = set(_task_service_functions())
    unknown = sorted((set(INTERNAL_ONLY) | set(SURFACE_DEBT)) - known)
    assert not unknown, (
        f"allowlist ссылается на несуществующие функции task_service: {unknown} — "
        "функция переименована или удалена, запись пора убрать."
    )
