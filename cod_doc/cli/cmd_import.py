"""COD-051: `cod-doc import` — bulk-load an existing repo into the DB."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console

from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import restate_importer

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config, ProjectEntry

console = Console()

# SYM-004: сколько путей печатать в `--dry-run`, прежде чем свернуть хвост.
# Разведка репозитория на 641 markdown не должна топить терминал.
_DRY_RUN_PREVIEW_LIMIT = 50

_EXCLUDE_HELP = (
    "Glob-паттерн исключения относительно корня репозитория; повторяемый: "
    "--exclude 'experiments/stand*' --exclude '*/_archive'. Паттерн сверяется "
    "и с путём файла, и с каждым его каталогом-предком, поэтому каталожный "
    "паттерн вычищает всё поддерево. Это НЕ gitignore: '*' перекрывает '/'."
)


@click.group("import")
def import_cmd() -> None:
    """Импорт документации/задач из репозитория проекта."""


def _open_session(
    cfg: Config, project_name: str
) -> tuple[ProjectEntry, sessionmaker[Session], Engine]:
    entry = cfg.get_project(project_name)
    if entry is None:
        raise click.ClickException(f"Проект не найден: {project_name}")
    factory, engine = db_for_entry(entry)
    return entry, factory, engine


def _resolve_project_name(positional: str | None, option: str | None) -> str:
    """SYM-004: позиционный PROJECT_NAME и алиас ``-p/--project`` — одно и то же.

    Семейство `import` исторически брало проект позиционным аргументом, а весь
    остальной CLI (`link`, `doc`, `reindex`) — через ``-p``. Обе формы теперь
    работают; указать разом две разных — ошибка, а не тихий выбор одной.
    """
    if positional and option and positional != option:
        raise click.ClickException(
            f"Проект указан дважды и по-разному: '{positional}' и '{option}'. Оставьте одно."
        )
    name = positional or option
    if not name:
        raise click.ClickException(
            "Не указан проект: cod-doc import <cmd> <PROJECT_NAME> либо -p/--project <slug>."
        )
    return name


def _project_db_id(session: Session, project_name: str) -> int:
    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        raise click.ClickException(
            f"Проект '{project_name}' не зарегистрирован в БД. Сначала: cod-doc project init <name>"
        )
    return proj.row_id


@import_cmd.command("docs")
@click.argument("project_name", required=False)
@click.option("--project", "-p", "project_opt", default=None, help="Слаг проекта (алиас).")
@click.option("--dry-run", is_flag=True, help="Показать план без записи.")
@click.option(
    "--max-files",
    type=int,
    default=restate_importer.DEFAULT_MAX_FILES,
    show_default=True,
    help="Cap на количество файлов в одном прогоне.",
)
@click.option("--exclude", "exclude", multiple=True, help=_EXCLUDE_HELP)
@click.option(
    "--limit",
    type=click.IntRange(min=0),
    default=_DRY_RUN_PREVIEW_LIMIT,
    show_default=True,
    help="Сколько путей печатать в --dry-run; 0 — весь список.",
)
@click.pass_context
def cmd_import_docs(
    ctx: click.Context,
    project_name: str | None,
    project_opt: str | None,
    dry_run: bool,
    max_files: int,
    exclude: tuple[str, ...],
    limit: int,
) -> None:
    """Импортировать .md/.rst/.txt файлы как Documents."""
    cfg: Config = ctx.obj["config"]
    name = _resolve_project_name(project_name, project_opt)
    entry, factory, engine = _open_session(cfg, name)
    try:
        with transactional(factory) as session:
            project_id = _project_db_id(session, name)
            summary = restate_importer.import_docs(
                session,
                repo_root=Path(entry.path),
                project_id=project_id,
                max_files=max_files,
                exclude=exclude,
            )
            if dry_run:
                session.rollback()
    finally:
        engine.dispose()

    if dry_run:
        console.print("[yellow]Dry-run — изменения откатили.[/yellow]")
    console.print(f"Imported: [bold green]{summary.imported}[/bold green]")
    console.print(f"Skipped (already in DB): {summary.skipped}")
    if dry_run and summary.files:
        # SYM-004: разведка без списка файлов бесполезна — по одним счётчикам
        # не видно, сработал ли --exclude. markup=False: '[' в имени файла
        # не должен уехать в rich-разметку; soft_wrap — чтобы длинный путь
        # не переносился по ширине терминала.
        # ADO-059 (friction #8): --limit 0 печатает весь список — на корпусе
        # 400+ документов решение по exclude иначе принимать не по чему.
        console.print(f"Файлы ({len(summary.files)}):")
        shown = summary.files if limit == 0 else summary.files[:limit]
        for rel in shown:
            console.print(f"  • {rel}", markup=False, highlight=False, soft_wrap=True)
        hidden = len(summary.files) - len(shown)
        if hidden > 0:
            console.print(f"  … ещё {hidden} (полный список: --limit 0)")
    if dry_run and summary.hidden_dirs:
        # ADO-061 (friction #11): скип скрытых каталогов больше не молчаливый.
        console.print(
            f"Скрытые каталоги пропущены ({len(summary.hidden_dirs)}): "
            + ", ".join(summary.hidden_dirs)
        )
    if summary.warnings:
        # ADO-015: frontmatter values coerced to fit an enum — reported, since
        # a bulk import of a foreign corpus is exactly where they hide.
        console.print(f"[yellow]Warnings: {len(summary.warnings)}[/yellow]")
        for w in summary.warnings[:10]:
            console.print(f"  ⚠️  {w}")
    if summary.errors:
        console.print(f"[red]Errors: {len(summary.errors)}[/red]")
        for e in summary.errors[:10]:
            console.print(f"  • {e}")


@import_cmd.command("legacy-tasks")
@click.argument("project_name", required=False)
@click.option("--project", "-p", "project_opt", default=None, help="Слаг проекта (алиас).")
@click.option("--dry-run", is_flag=True, help="Показать план без записи.")
@click.pass_context
def cmd_import_legacy_tasks(
    ctx: click.Context, project_name: str | None, project_opt: str | None, dry_run: bool
) -> None:
    """Перенести записи из .cod-doc/tasks.yaml в DB-таблицу task."""
    cfg: Config = ctx.obj["config"]
    name = _resolve_project_name(project_name, project_opt)
    entry, factory, engine = _open_session(cfg, name)
    yaml_path = entry.cod_doc_dir / "tasks.yaml"
    try:
        with transactional(factory) as session:
            project_id = _project_db_id(session, name)
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
@click.argument("project_name", required=False)
@click.option("--project", "-p", "project_opt", default=None, help="Слаг проекта (алиас).")
@click.option("--dry-run", is_flag=True)
@click.option("--exclude", "exclude", multiple=True, help=_EXCLUDE_HELP)
@click.pass_context
def cmd_import_all(
    ctx: click.Context,
    project_name: str | None,
    project_opt: str | None,
    dry_run: bool,
    exclude: tuple[str, ...],
) -> None:
    """Запустить все доступные пайплайны импорта подряд."""
    name = _resolve_project_name(project_name, project_opt)
    # exclude пробрасывается явно: ctx.invoke подставил бы click-дефолт (пустой
    # кортеж), и флаг молча не сработал бы на составном прогоне.
    ctx.invoke(
        cmd_import_docs,
        project_name=name,
        project_opt=None,
        dry_run=dry_run,
        max_files=restate_importer.DEFAULT_MAX_FILES,
        exclude=exclude,
    )
    ctx.invoke(cmd_import_legacy_tasks, project_name=name, project_opt=None, dry_run=dry_run)
