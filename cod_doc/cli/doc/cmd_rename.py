"""`doc rename` — rename a document key (and optionally path), cascade links."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("rename")
@click.argument("doc_key")
@click.argument("new_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--path", "new_path", default=None, help="New file path (optional)")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option("--no-cascade", is_flag=True, default=False, help="Skip link cascade update")
@click.pass_context
def doc_rename(
    ctx: click.Context,
    doc_key: str,
    new_key: str,
    project: str,
    new_path: str | None,
    author: str,
    reason: str | None,
    no_cascade: bool,
) -> None:
    """Rename a document key (and optionally its path); cascades incoming links."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services.doc_service import DocumentNotFoundError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                console.print(f"[red]Document '{doc_key}' not found.[/red]")
                sys.exit(1)
            doc_service.rename(
                session,
                document_id=d.row_id,
                new_doc_key=new_key,
                author=author,
                new_path=new_path,
                reason=reason,
                cascade_links=not no_cascade,
            )
    except DocumentNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    console.print(f"[green]✅ Renamed '{doc_key}' → '{new_key}'[/green]")
    if no_cascade:
        console.print("[dim]  (link cascade skipped)[/dim]")
