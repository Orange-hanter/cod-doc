"""`doc update` — change document metadata in the DB (mirror of MCP `doc_update`, DEBT-004)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc
from .cmd_create import _DOC_TYPE_CHOICES

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("update")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--title", default=None)
@click.option("--type", "doc_type", default=None, type=click.Choice(_DOC_TYPE_CHOICES))
@click.option("--owner", default=None, help="New owner; '' clears it")
@click.option(
    "--sensitivity",
    default=None,
    type=click.Choice(["public", "internal", "confidential", "restricted"]),
)
@click.option("--preamble", default=None, help="New preamble text")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and show what would change without writing anything",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_update(
    ctx: click.Context,
    doc_key: str,
    project: str,
    title: str | None,
    doc_type: str | None,
    owner: str | None,
    sensitivity: str | None,
    preamble: str | None,
    author: str,
    reason: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Change title, type, owner, sensitivity or preamble of DOC_KEY.

    Only the options you pass are changed. Status has its own command
    (`doc accept`). This replaces editing frontmatter and running `doc import`.
    """
    from cod_doc.domain.entities import DocumentType, Sensitivity
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services.validation import ValidationError

    if all(v is None for v in (title, doc_type, owner, sensitivity, preamble)):
        console.print(
            "[red]Pass at least one of --title/--type/--owner/--sensitivity/--preamble.[/red]"
        )
        sys.exit(1)

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf, commit=not dry_run) as session:
            project_id = _require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                console.print(f"[red]Document '{doc_key}' not found.[/red]")
                sys.exit(1)
            result = doc_service.update_metadata(
                session,
                document_id=d.row_id,
                author=author,
                title=title,
                type=DocumentType(doc_type) if doc_type is not None else None,
                owner=owner,
                sensitivity=Sensitivity(sensitivity) if sensitivity is not None else None,
                preamble=preamble,
                reason=reason,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)

    if as_json:
        payload = {"doc_key": doc_key, "changes": result.changes, "dry_run": dry_run}
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not result.changes:
        console.print(f"[yellow]⚪ Unchanged {doc_key}[/yellow]")
        return
    head = "🔍 Dry run" if dry_run else "✅ Updated"
    console.print(f"[green]{head} {doc_key}[/green]")
    for field, change in result.changes.items():
        # click.echo: values are user text and may contain rich markup brackets.
        click.echo(f"  {field}: {change['from']!r} → {change['to']!r}")
