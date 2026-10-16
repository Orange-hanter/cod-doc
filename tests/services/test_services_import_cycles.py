"""SVC-006: храповик на циклы импортов внутри `cod_doc/services`.

Цикл в ядре services вырос с 11 до 16 файлов за 83 коммита, и никто этого не
видел: интерпретатору он не мешает, потому что одна сторона каждого кольца
импортирует другую в теле функции («Local import to avoid a cycle»). Такой
импорт снимает ошибку загрузки, но не зависимость — модули по-прежнему не
понять и не поменять по отдельности.

Поэтому граф строится по AST и считает **все** импорты, в том числе в телах
функций; не считаются только импорты под `if TYPE_CHECKING:` — их нет во время
исполнения. Сторожится размер самой большой сильно связной компоненты: он может
только уменьшаться. Разорвал кольцо — опусти `MAX_SCC_SIZE` до нового значения,
тест подскажет его сам.

Что осталось внутри потолка и почему, — задача SVC-007 (каскад якорей
`doc_service ↔ link_service`, нужна развилка дизайна).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "cod_doc" / "services"
PACKAGE = "cod_doc.services"

#: Размер самой большой компоненты на момент SVC-001…006. Только вниз.
MAX_SCC_SIZE = 4


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_imports(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    """Все импорты модуля, кроме тех, что под `if TYPE_CHECKING:`."""
    found: list[ast.Import | ast.ImportFrom] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking_guard(node):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Import | ast.ImportFrom):
            found.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def _import_graph() -> dict[str, set[str]]:
    files = {_module_name(p): p for p in SERVICES_DIR.rglob("*.py") if "__pycache__" not in p.parts}
    graph: dict[str, set[str]] = {name: set() for name in files}

    def link(src: str, target: str) -> None:
        if target in files and target != src:
            graph[src].add(target)

    for src, path in files.items():
        is_package = path.name == "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _runtime_imports(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    link(src, alias.name)
                continue
            if node.level:
                base = src.split(".")
                # `from . import x` в `pkg/mod.py` — это `pkg`; в `pkg/__init__.py` — тоже `pkg`.
                drop = node.level - 1 if is_package else node.level
                base = base[: len(base) - drop]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            if not module.startswith(PACKAGE):
                continue
            # `from pkg import name`: если `name` — подмодуль, ребро идёт в него,
            # иначе в сам пакет (реэкспортированный символ).
            for alias in node.names:
                submodule = f"{module}.{alias.name}"
                link(src, submodule if submodule in files else module)
    return graph


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    """Тарьян, итеративно: глубина импортов не упирается в предел рекурсии."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0

    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, list[str]]] = [(root, sorted(graph[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, pending = work[-1]
            if pending:
                nxt = pending.pop()
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, sorted(graph[nxt])))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(sorted(component))
    return components


def _largest_cycle() -> list[str]:
    cycles = [c for c in _strongly_connected_components(_import_graph()) if len(c) > 1]
    return max(cycles, key=len, default=[])


def test_services_import_cycle_does_not_grow() -> None:
    largest = _largest_cycle()
    assert len(largest) <= MAX_SCC_SIZE, (
        f"цикл импортов в services вырос до {len(largest)} модулей (потолок {MAX_SCC_SIZE}): "
        f"{largest}. Импорт в теле функции цикл не разрывает — вынеси общее "
        "(исключения, чистые помощники) в листовой модуль."
    )


def test_ceiling_is_tight() -> None:
    """Разорвал кольцо — зафиксируй выигрыш, иначе потолок снова наберётся."""
    largest = _largest_cycle()
    assert len(largest) == MAX_SCC_SIZE, (
        f"самый большой цикл теперь {len(largest)} модулей: {largest}. "
        f"Опусти MAX_SCC_SIZE с {MAX_SCC_SIZE} до {len(largest)}."
    )


def test_graph_sees_function_level_imports() -> None:
    """Самопроверка разбора: импорт в теле функции — тоже ребро."""
    graph = _import_graph()
    # `run_context.record_step` пишет шаг через activity_service изнутри функции.
    assert "cod_doc.services.activity_service" in graph["cod_doc.services.run_context"]
