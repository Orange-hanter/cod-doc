"""`doc move-section` — reorder a section (mirror of MCP `doc_move_section`, DEBT-007)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("move-section")
@click.argument("doc_key")
@click.argument("anchor")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--position",
    type=int,
    required=True,
    help="Target index in the document, 0-based",
)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show the resulting order without writing anything",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_move_section(
    ctx: click.Context,
    doc_key: str,
    anchor: str,
    project: str,
    position: int,
    author: str,
    reason: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Move section ANCHOR of DOC_KEY to index --position.

    The section keeps its revision history; only the order changes.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services.doc_service import SectionNotFoundError
    from cod_doc.services.validation import ValidationError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf, commit=not dry_run) as session:
            project_id = _require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                console.print(f"[red]Document '{doc_key}' not found.[/red]")
                sys.exit(1)
            move = doc_service.move_section(
                session,
                document_id=d.row_id,
                anchor=anchor,
                position=position,
                author=author,
                reason=reason,
            )
    except SectionNotFoundError:
        console.print(f"[red]Section '{anchor}' not found in document '{doc_key}'.[/red]")
        sys.exit(1)
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)

    changed = move.from_position != move.to_position
    if as_json:
        payload = {
            "doc_key": doc_key,
            "anchor": anchor,
            "from": move.from_position,
            "to": move.to_position,
            "changed": changed,
            "order": move.order,
            "dry_run": dry_run,
        }
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not changed:
        console.print(f"[yellow]⚪ Unchanged {doc_key}#{anchor}: already at {position}[/yellow]")
        return
    head = "🔍 Dry run" if dry_run else "✅ Moved"
    console.print(
        f"[green]{head} {doc_key}#{anchor}: {move.from_position} → {move.to_position}[/green]"
    )
    for index, name in enumerate(move.order):
        click.echo(f"  {index}. {name}")
