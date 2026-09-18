"""Общий сканер паритета поверхностей: мутация в services обязана быть на MCP и CLI.

Закон репозитория (CLAUDE.md, «Четыре равные поверхности»): новая
функциональность в ``services/`` обязана появиться и в CLI, и в MCP — агент и
человек должны иметь тождественный интерфейс.

Метод — **обнаружение вместо ручного списка**. Множество «мутаций» вычисляется
из AST сервиса: публичная функция считается мутацией, если её call-graph (с
раскрытием module-local хелперов вроде ``_update_text_field``) содержит запись
в audit-trail: ``rev.write`` / ``activity_service.emit*`` /
``activity_service.write_revision_and_emit_event``. По правилу ADO-040
мутирующий сервис обязан писать revision и activity event, поэтому «пишет
audit-trail» ≡ «мутация» — новая функция попадает под проверку сама, без
правки теста. Ручной список проверяется только как смоук на сам детектор:
``known_mutations`` не даёт тесту «зеленеть» на пустом множестве.

Границу проверки надо назвать честно: сканер ловит **отсутствие функции** на
поверхности, а не расхождение сигнатур. Пробел вида «у CLI ``story create``
есть флаг ``--section``, а у MCP ``story_create`` параметра секции нет вовсе»
этим сканером НЕ ловится: ``create`` вызывается с обеих поверхностей, просто с
разным набором аргументов. Сверка параметров — отдельная работа с другой ценой
ложных срабатываний.

Поверхности — ``cod_doc/mcp/`` и ``cod_doc/cli/``, машинно-читаемые поверхности
из контракта ADO-067. Web (``api/``) и TUI не проверяются: человеческие
поверхности рендерят подмножество осознанно.

Модуль намеренно приватный (``_``-префикс): pytest его не собирает, а оба
теста-потребителя держат здесь только механику, оставляя у себя свои списки
сервисов и свою историю.

Потребители:

* ``test_task_mutation_surface_parity.py`` — ``task_service`` + ``story_service``
  (ADO-067, ADO-159);
* ``test_doc_mutation_surface_parity.py`` — ``doc_service`` (STO-017).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "cod_doc" / "services"
SERVICES_PACKAGE = "cod_doc.services"
SURFACE_DIRS = {
    "mcp": REPO_ROOT / "cod_doc" / "mcp",
    "cli": REPO_ROOT / "cod_doc" / "cli",
}

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

#: Минимальная длина обоснования в allowlist. Короче — это не объяснение,
#: а отписка, и запись превращается в шум (риск из контракта ADO-067).
MIN_REASON_LEN = 20


@dataclass(frozen=True)
class ServiceSpec:
    """Сервис под проверкой: где его код и как он выглядит на поверхностях."""

    #: Имя модуля/пакета внутри ``cod_doc/services``; оно же — имя, под
    #: которым сервис импортируют поверхности (``from ... import story_service``).
    name: str
    #: Функции, известные детектору: смоук против «зелени на пустом множестве».
    known_mutations: frozenset[str]
    #: Read-функции, которые детектор НЕ должен считать мутацией.
    known_reads: frozenset[str]
    #: Пишут audit-trail, но выставлять наружу неправильно — внутренние шаги
    #: чужого протокола, а не операции пользователя.
    internal_only: dict[str, str] = field(default_factory=dict)
    #: Ratchet: мутации, выставленные не на все поверхности. Значение —
    #: (поверхности, где функции нет; причина). Может только сокращаться.
    surface_debt: dict[str, tuple[frozenset[str], str]] = field(default_factory=dict)

    @property
    def module(self) -> str:
        return f"{SERVICES_PACKAGE}.{self.name}"

    @property
    def sources(self) -> list[Path]:
        """Файлы с кодом сервиса: один модуль либо все модули пакета.

        ``__init__.py`` пакета исключён намеренно — там только ре-экспорт,
        а разбор call-graph работает по module-local хелперам и обязан
        оставаться внутри одного файла.
        """
        single = SERVICES_DIR / f"{self.name}.py"
        if single.is_file():
            return [single]
        pkg = SERVICES_DIR / self.name
        return sorted(f for f in pkg.glob("*.py") if f.name != "__init__.py")


# --------------------------------------------------------------------------- #
# Обнаружение мутаций                                                          #
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


def _module_functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
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


def service_functions(spec: ServiceSpec) -> set[str]:
    """Все top-level функции сервиса — по всем его файлам."""
    return {name for path in spec.sources for name in _module_functions(path)}


def discovered_mutations(spec: ServiceSpec) -> set[str]:
    """Публичные функции сервиса, которые пишут audit-trail.

    Разбор идёт пофайлово: раскрытие module-local хелперов обязано
    оставаться внутри своего файла, иначе одноимённый приватный хелпер из
    соседнего модуля пакета даст ложное срабатывание.
    """
    found: set[str] = set()
    for path in spec.sources:
        funcs = _module_functions(path)
        found |= {
            name
            for name in funcs
            if not name.startswith("_") and _writes_audit_trail(name, funcs, set())
        }
    return found


# --------------------------------------------------------------------------- #
# Обнаружение использования на поверхностях                                    #
# --------------------------------------------------------------------------- #


def _members_used(py_file: Path, spec: ServiceSpec) -> set[str]:
    """Имена членов сервиса, к которым обращается файл поверхности.

    Учитывает три формы: ``from ...story_service import create``,
    ``from cod_doc.services import story_service`` + ``story_service.create``
    и алиас (``... import story_service as stories`` + ``stories.create``).
    """
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    used: set[str] = set()
    aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            # Пакет сервиса импортируют и целиком, и по подмодулям
            # (``...story_service.sections``) — считаем обе формы.
            if node.module == spec.module or node.module.startswith(f"{spec.module}."):
                used.update(a.name for a in node.names)
            elif node.module == SERVICES_PACKAGE:
                aliases.update(a.asname or a.name for a in node.names if a.name == spec.name)
        elif isinstance(node, ast.Import):
            aliases.update(a.asname or a.name for a in node.names if a.name == spec.module)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            head = _dotted(node)
            if not head:
                continue
            prefix, _, attr = head.rpartition(".")
            if prefix in aliases:
                used.add(attr)
    return used


def surface_members(surface: str, spec: ServiceSpec) -> set[str]:
    """Имена членов сервиса, к которым обращается вся поверхность."""
    used: set[str] = set()
    for py_file in SURFACE_DIRS[surface].rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        used |= _members_used(py_file, spec)
    return used


def _all_surfaces(spec: ServiceSpec) -> dict[str, set[str]]:
    return {name: surface_members(name, spec) for name in SURFACE_DIRS}


# --------------------------------------------------------------------------- #
# Проверки — тела ассертов, общие для всех потребителей                        #
# --------------------------------------------------------------------------- #


def check_discovery_finds_known_mutations(spec: ServiceSpec) -> None:
    """Смоук на сам детектор: без него тест мог бы «зеленеть» на пустом множестве."""
    found = discovered_mutations(spec)
    assert spec.known_mutations <= found, (
        f"детектор мутаций сломан на {spec.name}: не видит известные write-функции "
        f"{sorted(spec.known_mutations - found)}. Проверь AUDIT_WRITE_CALLS."
    )
    misread = sorted(spec.known_reads & found)
    assert not misread, f"детектор считает мутацией read-функции {spec.name}: {misread}"


def check_every_mutation_is_exposed(spec: ServiceSpec) -> None:
    """Мутация обязана быть вызвана из cod_doc/mcp/ и из cod_doc/cli/."""
    mutations = discovered_mutations(spec) - set(spec.internal_only)
    surfaces = _all_surfaces(spec)

    missing: dict[str, list[str]] = {}
    for fn in sorted(mutations):
        gaps = sorted(s for s, members in surfaces.items() if fn not in members)
        allowed = spec.surface_debt.get(fn, (frozenset(), ""))[0]
        unexplained = [s for s in gaps if s not in allowed]
        if unexplained:
            missing[fn] = unexplained

    assert not missing, (
        f"мутации {spec.name} не выставлены на все поверхности: "
        f"{missing}. Закон CLAUDE.md «Четыре равные поверхности»: добавь тул в "
        "cod_doc/mcp/tools/ и команду в cod_doc/cli/. "
        "Если функция принципиально внутренняя — внеси её в internal_only "
        "с обоснованием (одна строка на запись); если пробел осознанный и "
        "временный — в surface_debt, тоже с обоснованием."
    )


def check_surface_debt_ratchet_is_current(spec: ServiceSpec) -> None:
    """surface_debt может только сокращаться: закрытый пробел обязан уйти из списка."""
    surfaces = _all_surfaces(spec)
    stale: dict[str, list[str]] = {}
    for fn, (gaps, _reason) in spec.surface_debt.items():
        fixed = sorted(s for s in gaps if fn in surfaces[s])
        if fixed:
            stale[fn] = fixed
    assert not stale, (
        f"surface_debt протух у {spec.name} — эти функции уже выставлены: {stale}. "
        "Убери запись (или поверхность из неё): список ratchet, он только сокращается."
    )


def check_allowlists_carry_a_justification(spec: ServiceSpec) -> None:
    """Пустое обоснование превращает allowlist в шум — запрещено (риск из контракта ADO-067)."""
    blank = [
        name for name, reason in spec.internal_only.items() if len(reason.strip()) < MIN_REASON_LEN
    ]
    blank += [
        name
        for name, (_g, reason) in spec.surface_debt.items()
        if len(reason.strip()) < MIN_REASON_LEN
    ]
    assert not blank, f"записи allowlist без внятного обоснования: {blank}"

    known = service_functions(spec)
    unknown = sorted((set(spec.internal_only) | set(spec.surface_debt)) - known)
    assert not unknown, (
        f"allowlist ссылается на несуществующие функции {spec.name}: {unknown} — "
        "функция переименована или удалена, запись пора убрать."
    )


def check_spec_resolves_to_real_sources(spec: ServiceSpec) -> None:
    """Опечатка в имени сервиса дала бы пустой список файлов и зелёный тест ни о чём."""
    assert spec.sources, f"{spec.name}: не найдено ни одного файла с кодом сервиса"
    assert service_functions(spec), f"{spec.name}: в файлах нет top-level функций"
