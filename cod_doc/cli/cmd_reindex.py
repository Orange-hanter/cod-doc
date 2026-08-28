"""OBI-030: `cod-doc reindex --files <project>` — populate repo index."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from cod_doc.config import Config

console = Console()
log = get_logger("cli.reindex")


@click.group()
def reindex() -> None:
    """Rebuild repo-level indexes (files / symbols / imports)."""


@reindex.command("files")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--max-files", default=5000, show_default=True, type=int)
@click.pass_context
def reindex_files(ctx: click.Context, project: str, max_files: int) -> None:
    """Scan the project root + populate ``repo_file`` / ``_symbol`` / ``_import``."""
    from cod_doc.infra.db import db_for_entry, transactional
    from cod_doc.infra.repositories import ProjectRepository
    from cod_doc.services import repo_index_service

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(project)
    if not entry:
        console.print(f"[red]Project not found: {project}[/red]")
        sys.exit(1)

    factory, _engine = db_for_entry(entry)

    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(project)
        if proj is None or proj.row_id is None:
            console.print(f"[red]Project '{project}' not in DB.[/red]")
            sys.exit(1)
        result = repo_index_service.scan_project(
            session,
            project_id=proj.row_id,
            repo_path=Path(entry.path),
            max_files=max_files,
        )

    console.print(
        f"📚 [green]Reindexed[/green] {project}: "
        f"files=[cyan]{result['files']}[/cyan] · "
        f"symbols=[cyan]{result['symbols']}[/cyan] · "
        f"imports=[cyan]{result['imports']}[/cyan] · "
        f"[dim]skipped (.gitignore) {result['skipped_gitignore']}[/dim]"
    )
