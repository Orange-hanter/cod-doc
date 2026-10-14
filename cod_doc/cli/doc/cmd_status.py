"""`doc status` — set a document's lifecycle status (mirror of MCP `doc_update_status`, DEBT-005)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from cod_doc.domain.entities import DocumentStatus

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("status")
@click.argument("doc_key")
@click.argument("status", type=click.Choice([s.value for s in DocumentStatus]))
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show the transition without writing anything",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_status(
    ctx: click.Context,
    doc_key: str,
    status: str,
    project: str,
    author: str,
    reason: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Set STATUS on DOC_KEY — any lifecycle value, e.g. deprecated.

    `doc accept` stays the shortcut for DRAFT/REVIEW → ACTIVE. Setting the
    current status writes nothing.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf, commit=not dry_run) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        old_status = d.status.value
        doc_service.update_status(
            session,
            document_id=d.row_id,
            new_status=DocumentStatus(status),
            author=author,
            reason=reason,
        )

    changed = old_status != status
    if as_json:
        payload = {
            "doc_key": doc_key,
            "from": old_status,
            "to": status,
            "changed": changed,
            "dry_run": dry_run,
        }
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not changed:
        console.print(f"[yellow]⚪ Unchanged {doc_key}: already {status}[/yellow]")
        return
    head = "🔍 Dry run" if dry_run else "✅"
    console.print(f"[green]{head} {doc_key}: {old_status} → {status}[/green]")
