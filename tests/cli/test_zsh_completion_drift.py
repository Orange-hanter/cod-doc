"""Anti-drift: закоммиченный `_cod-doc` обязан совпадать с живым click-деревом.

Новая команда, новая опция или правка short_help роняют этот тест, пока
артефакт не регенерирован. В этом и смысл: completion, который отстал от CLI,
хуже отсутствующего — он врёт про существующие команды.
"""

from __future__ import annotations

import ast
import difflib
from pathlib import Path

import pytest

from cod_doc.cli import main as cli_root
from cod_doc.cli.completion import ARTIFACT_PATH, PRELUDE_PATH, REGEN_COMMAND
from cod_doc.cli.completion.zsh import ROOT_FUNC, render_zsh

_DIFF_BUDGET = 8000
_CLI_INIT = Path(__file__).resolve().parents[2] / "cod_doc" / "cli" / "__init__.py"


def _render() -> str:
    return render_zsh(cli_root, prelude=PRELUDE_PATH.read_text(encoding="utf-8"))


def test_artifact_matches_cli_tree() -> None:
    expected = _render()
    actual = ARTIFACT_PATH.read_text(encoding="utf-8")
    if actual == expected:
        return

    diff = "\n".join(
        difflib.unified_diff(
            actual.splitlines(),
            expected.splitlines(),
            fromfile="закоммичено: cod_doc/cli/completion/_cod-doc",
            tofile="сгенерировано из живого click-дерева",
            lineterm="",
            n=2,
        )
    )
    pytest.fail(
        "zsh-completion разошёлся с CLI. Регенерируй и закоммить:\n"
        f"    {REGEN_COMMAND}\n"
        "    git add cod_doc/cli/completion/_cod-doc\n"
        "(правка prelude.zsh или sources.py тоже требует регенерации — они\n"
        " вшиваются в артефакт)\n\n" + diff[:_DIFF_BUDGET]
    )


def test_render_is_deterministic() -> None:
    """Два прогона подряд дают байт-в-байт одно и то же."""
    assert _render() == _render()


def test_artifact_has_compdef_header_and_tail_call() -> None:
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert text.startswith("#compdef cod-doc\n"), "первая строка обязана быть #compdef"
    # Файл #compdef на первом вызове исполняется целиком, поэтому обязан
    # позвать собственную функцию в конце.
    assert text.rstrip().endswith(f'{ROOT_FUNC} "$@"')


def test_root_function_is_defined_unguarded() -> None:
    """Guard на корне даёт бесконечную рекурсию при автозагрузке.

    Тело файла #compdef СТАНОВИТСЯ телом функции-заглушки, поэтому к моменту
    исполнения `$+functions[_cod-doc]` уже 1. С guard'ом определение
    пропускается, и завершающий `_cod-doc "$@"` зовёт заглушку снова — до
    «maximum nested function level reached». `_git` по той же причине
    переопределяет себя безусловно. Ловилось только настоящим TAB в pty.
    """
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert f"\n{ROOT_FUNC}() {{\n" in text, "корень обязан определяться безусловно"
    assert f"$+functions[{ROOT_FUNC}]" not in text, "guard на корне ломает автозагрузку"


def test_helper_functions_are_guarded() -> None:
    """Вспомогательные, наоборот, guard'ятся — как `_git-add` в `_git`."""
    text = ARTIFACT_PATH.read_text(encoding="utf-8")
    assert "(( $+functions[_cod_doc_task_show] )) || _cod_doc_task_show()" in text


def test_completion_package_is_not_imported_at_cli_startup() -> None:
    """Старт CLI стоит ~470 мс; генератор не имеет права её увеличивать.

    Из `cod_doc/cli/__init__.py` разрешён ровно один импорт пакета — лёгкий
    `completion.cmd`. Всё остальное (обход дерева, рендер) грузится лениво.
    """
    tree = ast.parse(_CLI_INIT.read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    heavy = {
        module
        for module in imported
        if module.startswith("cod_doc.cli.completion") and module != "cod_doc.cli.completion.cmd"
    }
    assert heavy == set(), f"из cli/__init__ можно тянуть только completion.cmd, а тянется {heavy}"
