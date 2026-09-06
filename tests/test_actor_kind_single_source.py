"""ADO-044 / ADR-012: анти-drift — эвристика actor_kind живёт в одном месте.

Правило: значение ``activity_event.actor_kind`` выводится из строки-автора
**только** через :func:`cod_doc.domain.entities.actor_kind_for_author`.
Собственная эвристика на call-site'е (``author.startswith("agent")``,
``"run" in agent``, ``"human" if force else "orchestrator"``) запрещена —
именно из-за неё один и тот же прогон писался в журнал под разными
ролями.

Тест структурный, в стиле tests/services/test_services_layering.py:
обходит AST и ищет `actor_kind=<выражение>` в вызовах, где выражение —
не литерал и не вызов резолвера.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "cod_doc"

#: Единственный файл, которому позволено выводить роль из строки.
RESOLVER_FILE = PACKAGE_DIR / "domain" / "entities.py"

#: Тонкий ре-экспорт резолвера (см. ADR-012, решение 3).
REEXPORT_FILE = PACKAGE_DIR / "services" / "activity_service.py"

ALLOWED_CALLEES = {"actor_kind_for_author", "_actor_kind_for_author", "str", "ActorKind"}


def _python_files() -> list[Path]:
    return [p for p in PACKAGE_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def _actor_kind_expressions(py_file: Path) -> list[tuple[int, ast.expr]]:
    """Все выражения, подставляемые в keyword-аргумент ``actor_kind=``."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    found: list[tuple[int, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "actor_kind":
                found.append((kw.value.lineno, kw.value))
    return found


def test_actor_kind_is_never_derived_inline() -> None:
    """`actor_kind=` принимает литерал, переменную или вызов резолвера — не эвристику."""
    violations: list[str] = []
    for py_file in _python_files():
        for lineno, expr in _actor_kind_expressions(py_file):
            if isinstance(expr, ast.Constant | ast.Name | ast.Attribute):
                continue
            if isinstance(expr, ast.Call):
                callee = expr.func
                name = (
                    callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
                )
                if name in ALLOWED_CALLEES:
                    continue
            rel = py_file.relative_to(REPO_ROOT)
            violations.append(f"{rel}:{lineno} — {ast.unparse(expr)}")

    assert violations == [], (
        "actor_kind выводится инлайн вместо domain.entities.actor_kind_for_author() "
        "(ADR-012 / ADO-044):\n  " + "\n  ".join(violations)
    )


def test_startswith_agent_heuristic_lives_only_in_the_resolver() -> None:
    """Ни один файл, кроме резолвера, не классифицирует автора по префиксу сам."""
    banned = ('startswith("agent")', "startswith('agent')", 'startswith("orchestrator")')
    violations: list[str] = []
    for py_file in _python_files():
        if py_file in {RESOLVER_FILE, REEXPORT_FILE}:
            continue
        text = py_file.read_text(encoding="utf-8")
        for needle in banned:
            if needle in text:
                violations.append(f"{py_file.relative_to(REPO_ROOT)} — {needle}")

    assert violations == [], (
        "эвристика роли по префиксу автора должна жить только в "
        "cod_doc/domain/entities.py::actor_kind_for_author (ADR-012):\n  " + "\n  ".join(violations)
    )


def test_audit_log_is_gone_from_the_codebase() -> None:
    """ADR-012: таблица `audit_log` удалена — ORM-класса и импортов быть не должно."""
    offenders: list[str] = []
    for py_file in _python_files():
        if "migrations" in py_file.parts:
            continue  # исторические миграции описывают прошлое схемы
        if "AuditLogModel" in py_file.read_text(encoding="utf-8"):
            offenders.append(str(py_file.relative_to(REPO_ROOT)))
    assert offenders == [], f"AuditLogModel удалён по ADR-012, но всё ещё упоминается: {offenders}"
