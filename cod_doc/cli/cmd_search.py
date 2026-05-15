"""OBI-040: `cod-doc search <query>` — FTS5-backed unified search."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from cod_doc.config import Config

console = Console()
log = get_logger("cli.search")


@click.command()
@click.argument("query")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--scope",
    type=click.Choice(["task", "doc", "story", "adr"]),
    default=None,
    help="Restrict search to one kind",
)
@click.option("--limit", default=20, type=int, show_default=True)
@click.option("--reindex", is_flag=True, default=False,
              help="Rebuild the FTS index before searching.")
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    project: str,
    scope: str | None,
    limit: int,
    reindex: bool,
) -> None:
    """Search tasks / docs / stories / ADRs for QUERY (FTS5)."""
    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url, transactional
    from cod_doc.infra.repositories import ProjectRepository
    from cod_doc.services import search_service

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(project)
    if not entry:
        console.print(f"[red]Project not found: {project}[/red]")
        sys.exit(1)
    url = resolve_db_url(Path(entry.path))
    engine = make_engine(url)
    factory = make_session_factory(engine)

    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(project)
        if proj is None or proj.row_id is None:
            console.print(f"[red]Project '{project}' not in DB.[/red]")
            sys.exit(1)
        if reindex:
            counts = search_service.reindex_all(session, proj.row_id)
            console.print(f"📚 reindexed: {counts}")
        result = search_service.search(
            session, project_id=proj.row_id, query=query,
            scope=scope, limit=limit,
        )

    if result["total"] == 0:
        console.print(f"[dim]No hits for {query!r}.[/dim]")
        return

    for kind, hits in result["by_kind"].items():
        if not hits:
            continue
        table = Table(title=f"{kind.upper()} ({len(hits)})", show_header=True)
        table.add_column("Ref", style="cyan", no_wrap=True)
        table.add_column("Title")
        table.add_column("Snippet")
        for h in hits:
            snippet = h["snippet"].replace("<mark>", "[bold yellow]").replace("</mark>", "[/]")
            table.add_row(h["ref"], escape(h["title"][:60]), snippet)
        console.print(table)
