"""Регенерация закоммиченного ``_cod-doc``.

    python -m cod_doc.cli.completion --write    # перезаписать артефакт
    python -m cod_doc.cli.completion --check    # только проверить (exit 1 при дрейфе)
    python -m cod_doc.cli.completion            # напечатать в stdout

Отдельный ``__main__`` (а не подкоманда CLI) нужен потому, что генерация
импортирует всё дерево команд, а ``cod-doc completion zsh`` обязан оставаться
мгновенным — он лишь печатает готовый файл.
"""

from __future__ import annotations

import argparse
import sys

from cod_doc.cli import main as cli_root
from cod_doc.cli.completion import ARTIFACT_PATH, PRELUDE_PATH, REGEN_COMMAND
from cod_doc.cli.completion.zsh import render_zsh

_EXIT_DRIFT = 1


def build() -> str:
    """Отрендерить completion из живого click-дерева."""
    return render_zsh(cli_root, prelude=PRELUDE_PATH.read_text(encoding="utf-8"))


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cod_doc.cli.completion")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="перезаписать _cod-doc")
    group.add_argument("--check", action="store_true", help="проверить и выйти с кодом")
    args = parser.parse_args(argv)

    rendered = build()

    if args.write:
        ARTIFACT_PATH.write_text(rendered, encoding="utf-8")
        sys.stderr.write(f"записано: {ARTIFACT_PATH}\n")
        return 0

    if args.check:
        current = ARTIFACT_PATH.read_text(encoding="utf-8") if ARTIFACT_PATH.exists() else ""
        if current == rendered:
            return 0
        sys.stderr.write(f"_cod-doc разошёлся с CLI. Регенерируй: {REGEN_COMMAND}\n")
        return _EXIT_DRIFT

    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
