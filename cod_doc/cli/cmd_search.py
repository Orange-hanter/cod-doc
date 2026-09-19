"""OBI-040: `cod-doc search <query>` — FTS5-backed unified search."""

from __future__ import annotations

import sys
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
    type=click.Choice(["task", "doc", "story", "adr", "finding"]),
    default=None,
    help="Restrict search to one kind",
)
@click.option("--limit", default=20, type=int, show_default=True)
@click.option(
    "--projects",
    default=None,
    help="Comma-separated extra slugs to search together with --project (shared hub DB).",
)
@click.option(
    "--reindex", is_flag=True, default=False, help="Rebuild the FTS index before searching."
)
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    project: str,
    scope: str | None,
    limit: int,
    projects: str | None,
    reindex: bool,
) -> None:
    """Search tasks / docs / stories / ADRs for QUERY (FTS5).

    ``--projects a,b`` (CUR-013 / RFC 22 §3.6) widens the query to the given
    projects, which must share the ``db_url`` of ``--project``: one FTS5
    index means one bm25 scale, so ``--limit`` per kind applies to the merged
    result and every hit reports its owning project.
    """
    from cod_doc.infra.db import db_for_entry, transactional
    from cod_doc.infra.repositories import ProjectRepository
    from cod_doc.services import search_service

    cfg: Config = ctx.obj["config"]
    entry = cfg.get_project(project)
    if not entry:
        console.print(f"[red]Project not found: {project}[/red]")
        sys.exit(1)
    factory, _engine = db_for_entry(entry)
    slugs = search_service.split_project_slugs(projects)

    with transactional(factory) as session:
        proj = ProjectRepository(session).get_by_slug(project)
        if proj is None or proj.row_id is None:
            console.print(f"[red]Project '{project}' not in DB.[/red]")
            sys.exit(1)
        # Резолв кросс-проектных слагов — тем же сервисом, что и у MCP
        # ctx_search: единая проверка «все проекты в одной БД» (hub-режим).
        try:
            extra = (
                search_service.resolve_cross_project_ids(
                    session, project=project, projects=slugs, config=cfg
                )
                if slugs
                else {}
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        try:
            if reindex:
                for pid in (proj.row_id, *extra.values()):
                    counts = search_service.reindex_all(session, pid)
                    console.print(f"📚 reindexed: {counts}")
            result = search_service.search(
                session,
                project_id=proj.row_id,
                query=query,
                scope=scope,
                limit=limit,
                project_ids=list(extra.values()),
            )
        except search_service.SearchIndexMissing as exc:
            raise click.ClickException(str(exc)) from exc

    if result["total"] == 0:
        console.print(f"[dim]No hits for {query!r}.[/dim]")
        return

    for kind, hits in result["by_kind"].items():
        if not hits:
            continue
        table = Table(title=f"{kind.upper()} ({len(hits)})", show_header=True)
        table.add_column("Ref", style="cyan", no_wrap=True)
        if extra:
            table.add_column("Project", style="magenta", no_wrap=True)
        table.add_column("Title")
        table.add_column("Snippet")
        for h in hits:
            snippet = h["snippet"].replace("<mark>", "[bold yellow]").replace("</mark>", "[/]")
            row = [h["ref"], escape(h["title"][:60]), snippet]
            if extra:
                row.insert(1, h.get("project") or "—")
            table.add_row(*row)
        console.print(table)
