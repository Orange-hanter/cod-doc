"""COD-051: `cod-doc import` — bulk-load an existing repo into the DB."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console

from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import restate_importer

if TYPE_CHECKING:
    from cod_doc.config import Config

console = Console()


@click.group("import")
def import_cmd() -> None:
    """Импорт документации/задач из репозитория проекта."""


def _open_session(cfg: Config, project_name: str):  # type: ignore[no-untyped-def]
    entry = cfg.get_project(project_name)
    if entry is None:
        raise click.ClickException(f"Проект не найден: {project_name}")
    db_url = resolve_db_url(entry.root)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    return entry, factory, engine


def _project_db_id(session, project_name: str) -> int:  # type: ignore[no-untyped-def]
    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        raise click.ClickException(
            f"Проект '{project_name}' не зарегистрирован в БД. Сначала: cod-doc project init <name>"
        )
    return proj.row_id


@import_cmd.command("docs")
@click.argument("project_name")
@click.option("--dry-run", is_flag=True, help="Показать план без записи.")
@click.option(
    "--max-files",
    type=int,
    default=1000,
    show_default=True,
    help="Cap на количество файлов в одном прогоне.",
)
@click.pass_context
def cmd_import_docs(ctx: click.Context, project_name: str, dry_run: bool, max_files: int) -> None:
    """Импортировать .md/.rst/.txt файлы как Documents."""
    cfg: Config = ctx.obj["config"]
    entry, factory, engine = _open_session(cfg, project_name)
    try:
        with transactional(factory) as session:
            project_id = _project_db_id(session, project_name)
            summary = restate_importer.import_docs(
                session,
                repo_root=Path(entry.path),
                project_id=project_id,
                max_files=max_files,
            )
            if dry_run:
                session.rollback()
    finally:
        engine.dispose()

    if dry_run:
        console.print("[yellow]Dry-run — изменения откатили.[/yellow]")
    console.print(f"Imported: [bold green]{summary.imported}[/bold green]")
    console.print(f"Skipped (already in DB): {summary.skipped}")
    if summary.errors:
        console.print(f"[red]Errors: {len(summary.errors)}[/red]")
        for e in summary.errors[:10]:
            console.print(f"  • {e}")


@import_cmd.command("legacy-tasks")
@click.argument("project_name")
@click.option("--dry-run", is_flag=True, help="Показать план без записи.")
@click.pass_context
def cmd_import_legacy_tasks(ctx: click.Context, project_name: str, dry_run: bool) -> None:
    """Перенести записи из .cod-doc/tasks.yaml в DB-таблицу task."""
    cfg: Config = ctx.obj["config"]
    entry, factory, engine = _open_session(cfg, project_name)
    yaml_path = entry.cod_doc_dir / "tasks.yaml"
    try:
        with transactional(factory) as session:
            project_id = _project_db_id(session, project_name)
            summary = restate_importer.import_legacy_tasks(
                session,
                yaml_path=yaml_path,
                project_id=project_id,
            )
            if dry_run:
                session.rollback()
    finally:
        engine.dispose()

    if dry_run:
        console.print("[yellow]Dry-run — изменения откатили.[/yellow]")
    console.print(f"Imported: [bold green]{summary.imported}[/bold green]")
    console.print(f"Skipped: {summary.skipped}")
    if summary.plan_scope:
        console.print(f"Plan scope: [cyan]{summary.plan_scope}[/cyan]")
    if summary.errors:
        console.print(f"[red]Errors: {len(summary.errors)}[/red]")
        for e in summary.errors[:10]:
            console.print(f"  • {e}")


@import_cmd.command("all")
@click.argument("project_name")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def cmd_import_all(ctx: click.Context, project_name: str, dry_run: bool) -> None:
    """Запустить все доступные пайплайны импорта подряд."""
    ctx.invoke(cmd_import_docs, project_name=project_name, dry_run=dry_run, max_files=1000)
    ctx.invoke(cmd_import_legacy_tasks, project_name=project_name, dry_run=dry_run)
