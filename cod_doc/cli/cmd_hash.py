"""Команды хэширования: hash calc, hash update."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click
from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.config import Config, ProjectEntry

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
@click.option(
    "--project",
    "-p",
    default=None,
    help="Слаг проекта; по умолчанию — зарегистрированный проект, в корне которого лежит файл",
)
@click.option("--author", default="cli", show_default=True)
def hash_update(master_path: str, project: str | None, author: str) -> None:
    """Обновить все хэши в MASTER.md.

    Пересчёт — запись в проект, поэтому он оставляет activity-событие
    ``master.hashes_updated`` (ACU-002). Для события нужен проект: без
    ``--project`` он ищется по пути файла среди зарегистрированных. Файл вне
    зарегистрированных проектов пересчитывается как раньше, но без следа —
    об этом команда предупреждает.
    """
    from pathlib import Path

    from cod_doc.config import Config
    from cod_doc.core.hash_calc import update_hashes

    path = Path(master_path).expanduser().resolve()
    cfg = Config.load()
    entry = cfg.get_project(project) if project else _entry_owning(cfg, path)
    if project and entry is None:
        console.print(f"[red]Проект '{project}' не зарегистрирован.[/red]")
        sys.exit(1)

    if entry is None:
        n, warns = update_hashes(path)
        console.print(
            "[yellow]Файл вне зарегистрированных проектов — событие в журнал не записано.[/yellow]"
        )
    else:
        from cod_doc.infra.db import db_for_entry, transactional
        from cod_doc.infra.repositories import ProjectRepository
        from cod_doc.services import hash_service

        factory, engine = db_for_entry(entry)
        try:
            with transactional(factory) as session:
                row = ProjectRepository(session).get_by_slug(entry.name)
                if row is None or row.row_id is None:
                    console.print(f"[red]Проект '{entry.name}' не заведён в БД.[/red]")
                    sys.exit(1)
                n, warns = hash_service.update_master_hashes(
                    session, row.row_id, path, author=author
                )
        finally:
            engine.dispose()
    for w in warns:
        console.print(f"[yellow]{w}[/yellow]")
    console.print(f"[green]✅ Обновлено хэшей: {n}[/green]")


def _entry_owning(cfg: Config, path: Path) -> ProjectEntry | None:
    """Зарегистрированный проект с самым глубоким корнем, внутри которого лежит ``path``."""
    owners = [e for e in cfg.list_projects() if path.is_relative_to(e.root)]
    return max(owners, key=lambda e: len(e.root.parts), default=None)
