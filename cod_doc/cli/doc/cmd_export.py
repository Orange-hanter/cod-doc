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
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show the unified diff the export would apply; write nothing",
)
@click.option(
    "--force-write",
    is_flag=True,
    default=False,
    help="Overwrite an edited/foreign file (lifts the ADO-010 guards)",
)
@click.pass_context
def doc_export(
    ctx: click.Context,
    doc_key: str,
    project: str,
    force: bool,
    dry_run: bool,
    force_write: bool,
) -> None:
    """Export a document projection to disk (writes <project-root>/<doc.path>).

    Guards (ADO-010, audit finding F7): the export refuses to overwrite a file
    that does not match cod-doc's last export or import, and refuses to write
    into a repository that is not cod-doc's own checkout. `--dry-run` previews
    either case; `--force-write` proceeds anyway.
    """
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
        try:
            result = projection_service.export_document(
                session,
                d.row_id,
                root_path=root,
                force=force,
                dry_run=dry_run,
                force_write=force_write,
                own_checkout_only=True,
            )
        except projection_service.ExportGuardError as exc:
            console.print(f"[red]Refusing to export: {exc}[/red]")
            sys.exit(2)

    if dry_run:
        if result.diff:
            console.print(result.diff, markup=False, highlight=False)
        else:
            console.print(f"[dim]No changes: {result.path} already matches the projection.[/dim]")
        return
    if result.written:
        console.print(f"[green]✅ Exported to {result.path}[/green]")
    else:
        console.print(f"[dim]Skipped (already in sync): {result.path}[/dim]")
