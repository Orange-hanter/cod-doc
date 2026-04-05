"""TUI-команды: tui, wizard."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console

from cod_doc.config import Config, ProjectEntry
from cod_doc.logging_config import get_logger

console = Console()
log = get_logger("cli")


def _run_text_wizard(cfg: Config) -> None:
    """Fallback wizard for terminals where the Textual UI fails."""
    from cod_doc.core.project import Project

    console.print("[bold cyan]COD-DOC text wizard[/bold cyan]")
    console.print("Настройка через обычный терминал без TUI.\n")

    hide_api_key = sys.stdin.isatty() and sys.stdout.isatty()
    api_key = click.prompt("OpenRouter API key", hide_input=hide_api_key).strip()
    model = click.prompt(
        "LLM model",
        default="anthropic/claude-sonnet-4-6",
        show_default=True,
    ).strip()
    base_url = click.prompt(
        "Base URL",
        default="https://openrouter.ai/api/v1",
        show_default=True,
    ).strip()

    project_path = click.prompt(
        "Path to first project", default=str(Path.cwd()), show_default=True
    ).strip()
    project_name = click.prompt("Project name").strip()
    master_md = click.prompt(
        "Path to MASTER.md", default="MASTER.md", show_default=True
    ).strip()

    path = Path(project_path).expanduser().resolve()
    if not path.exists():
        raise click.ClickException(f"Директория не найдена: {path}")

    cfg.api_key = api_key
    cfg.model = model
    cfg.base_url = base_url or "https://openrouter.ai/api/v1"
    cfg.save()
    log.debug("Text wizard saved API config", extra={"event_type": "wizard_text_save_api"})

    entry = ProjectEntry(name=project_name, path=str(path), master_md=master_md or "MASTER.md")
    cfg.add_project(entry)
    Project(entry).init()
    log.debug(
        "Text wizard initialized project",
        extra={"event_type": "wizard_text_save_project", "project": project_name},
    )
    console.print(f"[green]✅ Настройка завершена. Проект '{project_name}' добавлен.[/green]")


@click.command()
@click.option("--debug-log-file", default=None, help="Путь к файлу debug-лога TUI")
@click.pass_context
def tui(ctx: click.Context, debug_log_file: str | None) -> None:
    """Запустить полный TUI (dashboard + wizard)."""
    from cod_doc.tui.app import CodDocApp

    app = CodDocApp(ctx.obj["config"], debug_log_file=debug_log_file)
    try:
        app.run()
    except Exception:
        log.exception("TUI launch failed")
        if debug_log_file:
            console.print(f"[red]TUI error. See debug log:[/red] {debug_log_file}")
        raise


@click.command()
@click.option("--debug-log-file", default=None, help="Путь к файлу debug-лога wizard")
@click.option(
    "--text", "text_mode", is_flag=True, default=False,
    help="Запустить текстовый wizard без TUI",
)
@click.pass_context
def wizard(ctx: click.Context, debug_log_file: str | None, text_mode: bool) -> None:
    """Запустить мастер настройки."""
    cfg = ctx.obj["config"]
    if text_mode:
        _run_text_wizard(cfg)
        return

    from cod_doc.tui.app import CodDocApp

    # Сбросить конфиг чтобы принудительно показать wizard
    cfg.api_key = ""
    app = CodDocApp(cfg, debug_log_file=debug_log_file)
    try:
        app.run()
    except Exception:
        log.exception("Wizard launch failed")
        if debug_log_file:
            console.print(f"[red]Wizard error. See debug log:[/red] {debug_log_file}")
        console.print("[yellow]Переключаюсь на текстовый wizard.[/yellow]")
        _run_text_wizard(cfg)
