"""ADO-208 (RFC 26 §5.1, задача 6): презентация не пишет ORM мимо services/.

Чем гейт отличается от сканера паритета. ``tests/services/_surface_parity.py``
опознаёт мутацию по признаку «функция пишет audit-trail» (revision / activity
event) и требует, чтобы такая функция была выставлена на MCP и CLI. Запись,
которая audit-trail не пишет и в ``services/`` не заходит, для него не
мутация по определению: исходная дыра ``api/legacy_tasks.py`` (план и секция
создавались прямо через репозитории) не могла упасть в CI. Этот гейт стережёт
другое — не «есть ли функция на поверхности», а «не течёт ли запись мимо
слоя»: в ``cod_doc/mcp/``, ``cod_doc/cli/``, ``cod_doc/api/`` запрещены
``session.add/add_all/delete/merge`` и ``*Repository(...).add/update/delete``.

Почему правило задаётся по получателю вызова, а не по имени метода. В
презентации есть вызовы, неотличимые по имени от нарушения:
``routine_service.delete(session, ...)`` в ``mcp/tools/routine_tools.py`` и
``api/web/pages/routines.py``, ``comment_service.delete(session, ...)`` в
``api/web/pages/comments.py``. Это ровно то, чего гейт хочет, — запись через
сервис; правило «запрещён ``.delete(``» покрасило бы их, и allowlist пришлось
бы заводить на первом же прогоне. Нарушение — только когда получатель сам
``session`` или репозиторий.

Почему отслеживаются локальные присваивания. Прямой
``XRepository(session).add(...)`` и форма ``repo = XRepository(session)`` с
последующим ``repo.add(...)`` — разные AST-узлы; вторая жила в
``api/legacy_tasks.py``, и без отслеживания присваиваний гейт зазеленел бы на
живом нарушении. Скоуп связывания — тело функции (вложенная функция — свой
скоуп), для модульного кода — модуль.

Остаточный долг: ``cod_doc/services/restate_importer.py`` создаёт секцию плана
напрямую через репозиторий, минуя ``plan_service``. Он лежит в ``services/``
и под этот гейт не попадает — гейт сканирует только презентацию.

Allowlist пуст и должен оставаться пустым.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRESENTATION_DIRS = ("cod_doc/mcp", "cod_doc/cli", "cod_doc/api")

SESSION_WRITE_METHODS = frozenset({"add", "add_all", "delete", "merge"})
REPOSITORY_WRITE_METHODS = frozenset({"add", "update", "delete"})

# (путь от корня репозитория, lineno) — сознательные исключения. Пуст.
ALLOWLIST: frozenset[tuple[str, int]] = frozenset()


def _repository_name(node: ast.expr) -> str | None:
    """Имя класса, если ``node`` — вызов ``*Repository(...)``."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return None
    return name if name.endswith("Repository") else None


class _OrmWriteFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        # Стек скоупов: имя переменной → класс репозитория, к которому она привязана.
        self.scopes: list[dict[str, str]] = [{}]
        self.violations: list[tuple[int, str]] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes.append({})
        self.generic_visit(node)
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _bind(self, target: ast.expr, value: ast.expr | None) -> None:
        if not isinstance(target, ast.Name):
            return
        repo = _repository_name(value) if value is not None else None
        if repo is None:
            self.scopes[-1].pop(target.id, None)
        else:
            self.scopes[-1][target.id] = repo

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        for target in node.targets:
            self._bind(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if node.value is not None:
            self._bind(node.target, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            receiver = func.value
            method = func.attr
            if (
                isinstance(receiver, ast.Name)
                and receiver.id == "session"
                and method in SESSION_WRITE_METHODS
            ):
                self.violations.append((node.lineno, f"session.{method}"))
            elif method in REPOSITORY_WRITE_METHODS:
                repo = _repository_name(receiver)
                if repo is None and isinstance(receiver, ast.Name):
                    repo = self.scopes[-1].get(receiver.id)
                if repo is not None:
                    self.violations.append((node.lineno, f"{repo}.{method}"))
        self.generic_visit(node)


def find_orm_writes(source: str, filename: str) -> list[tuple[int, str]]:
    """Прямые ORM-записи в исходнике: [(lineno, 'session.add' | 'XRepository.add'), ...]."""
    finder = _OrmWriteFinder()
    finder.visit(ast.parse(source, filename=filename))
    return sorted(finder.violations)


def test_direct_form_is_caught() -> None:
    source = (
        "PlanSectionRepository(session).add(x)\n"
        "session.add(m)\n"
        "session.delete(m)\n"
        "session.merge(m)\n"
    )
    assert find_orm_writes(source, "<direct>") == [
        (1, "PlanSectionRepository.add"),
        (2, "session.add"),
        (3, "session.delete"),
        (4, "session.merge"),
    ]


def test_local_variable_form_is_caught() -> None:
    """Регрессия формы, жившей в api/legacy_tasks.py."""
    source = "def f(s):\n    repo = XRepository(s)\n    repo.add(y)\n"
    assert find_orm_writes(source, "<local>") == [(3, "XRepository.add")]


def test_local_variable_scope_is_per_function() -> None:
    source = (
        "def f(s):\n"
        "    repo = XRepository(s)\n"
        "\n"
        "def g(session):\n"
        "    repo = routine_service\n"
        "    repo.delete(session, 1, 'x')\n"
        "\n"
        "def h(session):\n"
        "    repo.delete(session, 2)\n"
    )
    assert find_orm_writes(source, "<scope>") == []


def test_service_calls_are_not_violations() -> None:
    source = (
        "def f(session):\n"
        "    routine_service.delete(session, 1, 'x')\n"
        "    comment_service.delete(session, 5)\n"
        "    comments.delete(session, 5)\n"
        "    ProjectRepository(session).get_by_slug('p')\n"
        "    section_repo = PlanSectionRepository(session)\n"
        "    section_repo.list_for_plan(1)\n"
    )
    assert find_orm_writes(source, "<services>") == []


def test_live_presentation_has_no_orm_writes() -> None:
    files = sorted(
        path
        for directory in PRESENTATION_DIRS
        for path in (REPO_ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    assert len(files) >= 50

    violations = [
        (rel, lineno, what)
        for path in files
        for rel in [path.relative_to(REPO_ROOT).as_posix()]
        for lineno, what in find_orm_writes(path.read_text(encoding="utf-8"), rel)
        if (rel, lineno) not in ALLOWLIST
    ]
    assert violations == []


def test_allowlist_is_empty() -> None:
    assert frozenset() == ALLOWLIST


def test_restate_importer_named_as_debt() -> None:
    assert __doc__ is not None
    assert "restate_importer" in __doc__
