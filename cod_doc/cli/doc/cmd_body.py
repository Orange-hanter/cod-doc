"""`doc body` — print the full rendered body of a document."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("body")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.pass_context
def doc_body(ctx: click.Context, doc_key: str, project: str) -> None:
    """Print the full rendered body of a document."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        body = doc_service.render_body(session, d.row_id)

    if body is None:
        console.print("[dim](empty)[/dim]")
        return
    console.print(body, markup=False, highlight=False)
