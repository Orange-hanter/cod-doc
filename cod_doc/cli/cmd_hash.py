"""Команды хэширования: hash calc, hash update."""

from __future__ import annotations

import sys

import click
from rich.console import Console

console = Console()


@click.group()
def hash() -> None:
    """Утилиты хэширования."""


@hash.command("calc")
@click.argument("file_path")
def hash_calc(file_path: str) -> None:
    """Вычислить SHA-256 хэш файла."""
    from cod_doc.core.hash_calc import calc_hash as _calc

    try:
        h = _calc(file_path)
        console.print(f"sha:{h}  {file_path}")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


@hash.command("update")
@click.argument("master_path", default="MASTER.md")
def hash_update(master_path: str) -> None:
    """Обновить все хэши в MASTER.md."""
    from pathlib import Path
    from cod_doc.core.hash_calc import update_hashes

    n, warns = update_hashes(Path(master_path))
    for w in warns:
        console.print(f"[yellow]{w}[/yellow]")
    console.print(f"[green]✅ Обновлено хэшей: {n}[/green]")
