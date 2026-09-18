"""ADO-179: импорт CLI не должен тянуть SQLAlchemy и Alembic.

`cod_doc/cli/__init__.py` импортирует все 26 групп, чтобы зарегистрировать их
в click. Тела команд делают ленивые импорты, но четыре группы тянули
`infra`/`services` на уровне модуля — и за это платил каждый вызов, включая
`--help`: ~390 мс на импорт и ~700 мс на процесс.

Проверяем СОСТАВ `sys.modules`, а не секунды: замер времени на CI будет
флакать, а состав детерминирован. Тот же приём, что в
`test_zsh_completion_drift.py::test_completion_package_is_not_imported_at_cli_startup`.

Запускаем в отдельном процессе: в общем интерпретаторе pytest давно загрузил
и SQLAlchemy, и Alembic через фикстуры, поэтому проверка внутри него всегда
была бы ложноположительной.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[2] / "cod_doc" / "cli"

#: Что нельзя импортировать на уровне модуля в `cod_doc/cli/**`: эти корни
#: транзитивно приводят к SQLAlchemy.
_HEAVY_ROOTS = ("cod_doc.infra", "cod_doc.services", "sqlalchemy", "alembic")

#: Исключения с обоснованием. Пополнять только вместе с доказательством, что
#: импорт дешёвый: настоящий гейт — тест на `sys.modules` выше, а этот список
#: лишь описывает, почему конкретное место не считается нарушением.
_ALLOWED: dict[str, str] = {
    # `INGEST_ADAPTERS` нужен на уровне модуля: по нему в цикле регистрируется
    # подкоманда на каждый адаптер, то есть от него зависит состав дерева
    # команд. Импорт дешёвый (~1 мс): сам `registry` лёгкий, а тяжесть давал
    # `ingest_service/__init__` через `models` → `finding_service`, и она
    # убрана в ADO-179. Альтернатива — статический список имён в CLI — вводила
    # бы дубликат, обязанный разъезжаться.
    "cod_doc/cli/cmd_ingest.py": "cod_doc.services.ingest_service.registry",
}

_PROBE = (
    "import sys\n"
    "import cod_doc.cli\n"
    "print(','.join(n for n in sys.modules if n in ('sqlalchemy', 'alembic')))\n"
)


def test_importing_cli_does_not_load_sqlalchemy_or_alembic() -> None:
    """Главная проверка: смотрим на живой импорт, а не на текст модулей."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = [n for n in proc.stdout.strip().split(",") if n]
    assert not loaded, (
        "импорт cod_doc.cli подтянул " + ", ".join(loaded) + ".\n"
        "Скорее всего в какой-то группе появился импорт infra/services на "
        "уровне модуля — перенеси его в тело команды (ADO-179)."
    )


def _module_level_heavy_imports(path: Path) -> list[str]:
    """Импорты тяжёлых корней в module scope — тела функций не считаем."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        else:
            continue
        found += [n for n in names if any(n == r or n.startswith(r + ".") for r in _HEAVY_ROOTS)]
    return found


def test_no_cli_module_imports_infra_or_services_at_module_scope() -> None:
    """Сообщает, ГДЕ именно сломалось, — первый тест говорит только «сломалось».

    Не дублирует проверку выше: тест на `sys.modules` поймает и обходной путь
    через другой пакет, а этот укажет файл и строку в типичном случае.
    """
    offenders: dict[str, list[str]] = {}
    for path in sorted(CLI_ROOT.rglob("*.py")):
        rel = str(path.relative_to(CLI_ROOT.parents[1]))
        heavy = [m for m in _module_level_heavy_imports(path) if _ALLOWED.get(rel) != m]
        if heavy:
            offenders[rel] = heavy

    assert not offenders, "импорты infra/services на уровне модуля:\n" + "\n".join(
        f"  {path}: {', '.join(mods)}" for path, mods in offenders.items()
    )
