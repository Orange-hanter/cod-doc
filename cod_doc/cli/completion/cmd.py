"""``cod-doc completion zsh`` — напечатать готовый completion.

Команда НЕ регенерирует: она печатает закоммиченный артефакт дословно. Так
она остаётся мгновенной (одно чтение файла вместо обхода дерева команд) и
байт-в-байт совпадает с тем, что проверил CI.

Модуль держим пустым по импортам: его тянет ``cod_doc/cli/__init__.py``, а
старт CLI и так стоит ~470 мс.
"""

from __future__ import annotations

import click


@click.group()
def completion() -> None:
    """Shell-completion для cod-doc."""


@completion.command("zsh")
def completion_zsh() -> None:
    """Напечатать zsh-completion (ставится симлинком, см. scripts/)."""
    from cod_doc.cli.completion import ARTIFACT_PATH, REGEN_COMMAND

    if not ARTIFACT_PATH.exists():
        raise click.ClickException(
            f"артефакт не собран: {ARTIFACT_PATH}\nсобери командой: {REGEN_COMMAND}"
        )
    # click.echo, а не rich.Console: Console переносит длинные строки по ширине
    # терминала и порвала бы zsh-спеки.
    click.echo(ARTIFACT_PATH.read_text(encoding="utf-8"), nl=False)
