"""`doc export` — write the document projection to disk."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _get_root_path, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("export")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--force", is_flag=True, default=False, help="Re-export even if hash matches")
@click.pass_context
def doc_export(ctx: click.Context, doc_key: str, project: str, force: bool) -> None:
    """Export a document projection to disk (writes <project-root>/<doc.path>)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service, projection_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        result = projection_service.export_document(session, d.row_id, root_path=root, force=force)

    if result.written:
        console.print(f"[green]✅ Exported to {result.path}[/green]")
    else:
        console.print(f"[dim]Skipped (already in sync): {result.path}[/dim]")
