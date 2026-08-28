"""Команды управления глобальной hub-БД COD-DOC."""

from __future__ import annotations

import sqlite3

import click
from rich.console import Console

from cod_doc.logging_config import get_logger
from cod_doc.services import hub_service

console = Console()
log = get_logger("cli.hub")


@click.group()
def hub() -> None:
    """Управление глобальной hub-БД (мультипроектный реестр)."""


@hub.command("init")
def hub_init() -> None:
    """Создать/мигрировать ~/.cod-doc/hub.db (идемпотентно)."""
    db_path = hub_service.init_hub()

    # Убедиться, что WAL включён (SYM-002).
    with sqlite3.connect(str(db_path)) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    if journal_mode.lower() != "wal":
        raise click.ClickException(f"hub.db создан без WAL (journal_mode={journal_mode!r})")

    console.print(
        f"[green]✅ Hub-БД готова:[/green] [cyan]{db_path}[/cyan] "
        f"([dim]journal_mode={journal_mode}[/dim])"
    )
