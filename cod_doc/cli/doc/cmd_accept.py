"""`doc accept` — promote a DRAFT/REVIEW document to ACTIVE (COD-052 accept step)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("accept")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="human:cli", help="Author recorded on the transition")
@click.option("--reason", default=None, help="Optional reason for the acceptance")
@click.pass_context
def doc_accept(
    ctx: click.Context,
    doc_key: str,
    project: str,
    author: str,
    reason: str | None,
) -> None:
    """Promote a DRAFT/REVIEW document to ACTIVE (writes a revision)."""
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
        updated = doc_service.accept(session, document_id=d.row_id, author=author, reason=reason)
        status = updated.status.value

    console.print(f"[green]✓ Accepted[/green] {doc_key} → [cyan]{status}[/cyan]")
