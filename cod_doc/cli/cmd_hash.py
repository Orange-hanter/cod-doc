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
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Показать протухшие записи реестра, ничего не записывая (зеркало MCP master_hash_update)",
)
def hash_update(master_path: str, project: str | None, author: str, dry_run: bool) -> None:
    """Обновить все хэши в MASTER.md.

    Пересчёт — запись в проект, поэтому он оставляет activity-событие
    ``master.hashes_updated`` (ACU-002). Если ``MASTER.md`` — документ
    проекта, реестр пересчитывается в БД, а файл не трогается (DEBT-009):
    ``doc import MASTER.md`` после этого не нужен. Для события нужен проект: без
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

    if dry_run:
        _print_stale(path, entry)
        return

    if entry is None:
        n, warns = update_hashes(path)
        console.print(
            "[yellow]Файл вне зарегистрированных проектов — событие в журнал не записано.[/yellow]"
        )
    else:
        from cod_doc.cli._common import require_project_id
        from cod_doc.infra.db import db_for_entry, transactional
        from cod_doc.services import hash_service

        factory, engine = db_for_entry(entry)
        try:
            with transactional(factory) as session:
                project_id = require_project_id(session, entry.name)
                n, warns = hash_service.update_master_hashes(
                    session, project_id, path, author=author, root_path=entry.root
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


def _print_stale(path: Path, entry: ProjectEntry | None) -> None:
    """``--dry-run``: находки реестра — из БД, если ``MASTER.md`` заведён документом."""
    from cod_doc.core.hash_calc import stale_refs_in

    if entry is None:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        findings = stale_refs_in(text, path.parent)
    else:
        from cod_doc.cli._common import require_project_id
        from cod_doc.infra.db import db_for_entry, transactional
        from cod_doc.services import hash_service

        factory, engine = db_for_entry(entry)
        try:
            with transactional(factory, commit=False) as session:
                project_id = require_project_id(session, entry.name)
                findings = hash_service.stale_refs(session, project_id, path, entry.root)
        finally:
            engine.dispose()
    for f in findings:
        click.echo(f"{f['status']}: {f['path']} {f.get('expected', '')}→{f.get('actual', '?')}")
    click.echo(f"Протухших записей: {len(findings)} (dry run — ничего не записано)")
