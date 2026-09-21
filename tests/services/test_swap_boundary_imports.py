"""ADO-192: импорты по обе стороны границы свапа рантайма.

После ``runtime_service.swap()`` каталог ``~/.cod-doc/runtime`` — уже НОВОЕ
дерево, а `sys.path` живого процесса хранит абсолютный путь к его
``site-packages``. Значит, любой импорт, исполнившийся после свапа, придёт из
новой ревизии, тогда как всё импортированное до него осталось из старой. Один
процесс — две ревизии.

Отсюда два правила, противоположных по форме и одинаковых по причине:

1. ``runtime_service`` и ``launchd_service`` исполняются ПОСЛЕ свапа, поэтому
   импортируют только stdlib — ни на уровне модуля, ни в телах функций там не
   должно быть ничего, что лежит в ``site-packages`` рантайма;
2. ``update_service`` импортирует всё на уровне модуля, то есть ДО свапа.
   Ленивый импорт в теле его функции — инверсия правила CLI (ADO-179) и ровно
   тот баг, ради которого построен subprocess-relay. Инстинкт ревьюера
   «сделай лениво, это дорого» вернёт его, если не стеречь.

Проверяем текст модулей (AST), а не живой ``sys.modules``: ``import
cod_doc.services.update_service`` по построению тянет пол-проекта, и наблюдать
за составом загруженного здесь нечего. Тела под ``if TYPE_CHECKING:`` не
исполняются в рантайме и нарушением не считаются.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SERVICES_ROOT = Path(__file__).resolve().parents[2] / "cod_doc" / "services"

#: Модули, исполняющиеся по ту сторону свапа: только stdlib.
STDLIB_ONLY = ("runtime_service.py", "launchd_service.py")

#: Модуль-оркестратор: всё импортируется до свапа, то есть на уровне модуля.
EAGER_ONLY = "update_service.py"

_WHY_STDLIB_ONLY = (
    "Эти модули исполняются, когда ~/.cod-doc/runtime уже подменён: sys.path "
    "хранит абсолютный путь к его site-packages, и импорт cod_doc.*/click/rich/"
    "httpx/sqlalchemy после свапа придёт уже из НОВОГО дерева — в одном процессе "
    "смешаются две ревизии. Бери stdlib (urllib.request вместо httpx) или прячь "
    "импорт под `if TYPE_CHECKING:`."
)

_WHY_EAGER_ONLY = (
    "update_service живёт по обе стороны свапа, и отложенный импорт исполнится "
    "уже ПОСЛЕ подмены рантайма: половина процесса останется старой ревизией, "
    "половина приедет из новой. Это инверсия правила CLI (ADO-179): здесь импорт "
    "обязан быть на уровне модуля, то есть до свапа."
)


@dataclass(frozen=True, slots=True)
class _ImportSite:
    """Один импорт: корневой модуль, строка и то, в теле ли функции он стоит."""

    root: str
    lineno: int
    in_function: bool


def _is_type_checking(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` в обоих написаниях — голым именем и через `typing.`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _import_roots(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Корни импортируемых модулей; относительный импорт — заведомо не stdlib."""
    if isinstance(node, ast.Import):
        return [alias.name.split(".")[0] for alias in node.names]
    if node.level:
        return ["." * node.level + (node.module or "")]
    return [(node.module or "").split(".")[0]]


def _import_sites(source: str) -> list[_ImportSite]:
    """Все импорты модуля, кроме спрятанных под `if TYPE_CHECKING:`."""
    sites: list[_ImportSite] = []

    def walk(node: ast.AST, *, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                # Тело не исполняется в рантайме; ветка `else` — исполняется.
                for fallback in child.orelse:
                    walk(fallback, in_function=in_function)
                continue
            if isinstance(child, ast.Import | ast.ImportFrom):
                sites.extend(
                    _ImportSite(root=root, lineno=child.lineno, in_function=in_function)
                    for root in _import_roots(child)
                )
                continue
            nested = isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            walk(child, in_function=in_function or nested)

    walk(ast.parse(source), in_function=False)
    return sites


def _sites_of(name: str) -> list[_ImportSite]:
    path = SERVICES_ROOT / name
    assert path.is_file(), f"нет модуля {path} — переименовали? гейт границы свапа ослеп"
    return _import_sites(path.read_text(encoding="utf-8"))


def test_swap_side_modules_import_only_stdlib() -> None:
    """runtime_service и launchd_service — stdlib и ничего кроме."""
    offenders: list[str] = []
    for name in STDLIB_ONLY:
        offenders += [
            f"  cod_doc/services/{name}:{site.lineno}: {site.root}"
            for site in _sites_of(name)
            if site.root not in sys.stdlib_module_names
        ]
    assert not offenders, "импорт не из stdlib по ту сторону свапа:\n" + "\n".join(
        [*offenders, "", _WHY_STDLIB_ONLY]
    )


def test_update_service_has_no_lazy_imports() -> None:
    """У оркестратора все импорты на уровне модуля — то есть до свапа."""
    offenders = [
        f"  cod_doc/services/{EAGER_ONLY}:{site.lineno}: {site.root}"
        for site in _sites_of(EAGER_ONLY)
        if site.in_function
    ]
    assert not offenders, "импорт в теле функции:\n" + "\n".join([*offenders, "", _WHY_EAGER_ONLY])


def test_gate_sees_imports_inside_functions() -> None:
    """Гейт не вакуумный: на синтетическом модуле он находит оба вида нарушений."""
    sites = _import_sites(
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from cod_doc.config import Config\n"
        "def f():\n"
        "    import httpx\n"
        "    from . import sibling\n"
        "    return httpx, sibling\n"
    )
    roots = {site.root: site for site in sites}
    assert "cod_doc" not in roots, (
        "импорт под TYPE_CHECKING не исполняется и нарушением не является"
    )
    assert roots["httpx"].in_function is True
    assert roots["httpx"].root not in sys.stdlib_module_names
    assert any(root.startswith(".") for root in roots), "относительный импорт тоже не stdlib"
    assert roots["typing"].in_function is False
