"""`doc rename-section` — rename a section heading/anchor (mirror of MCP `doc_rename_section`, DEBT-008)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("rename-section")
@click.argument("doc_key")
@click.argument("anchor")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--heading", required=True, help="New heading text")
@click.option(
    "--anchor",
    "new_anchor",
    default=None,
    help="New anchor; default — slug of --heading, as doc import derives it",
)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show the rename and the links that would move, write nothing",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_rename_section(
    ctx: click.Context,
    doc_key: str,
    anchor: str,
    project: str,
    heading: str,
    new_anchor: str | None,
    author: str,
    reason: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Rename section ANCHOR of DOC_KEY; links to the old anchor are rewritten."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services.doc_service import SectionAlreadyExistsError, SectionNotFoundError
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
            result = doc_service.rename_section(
                session,
                project_id=project_id,
                document_id=d.row_id,
                anchor=anchor,
                heading=heading,
                new_anchor=new_anchor,
                author=author,
                reason=reason,
            )
    except SectionNotFoundError:
        console.print(f"[red]Section '{anchor}' not found in document '{doc_key}'.[/red]")
        sys.exit(1)
    except (SectionAlreadyExistsError, ValidationError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    to_anchor = result.section.anchor
    if as_json:
        payload = {
            "doc_key": doc_key,
            "from": {"anchor": result.old_anchor, "heading": result.old_heading},
            "to": {"anchor": to_anchor, "heading": result.section.heading},
            "rewritten": result.rewritten,
            "links": result.links,
            "dry_run": dry_run,
        }
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    head = "🔍 Dry run" if dry_run else "✅ Renamed"
    # click.echo: headings are user text and may contain rich markup brackets.
    click.echo(f"{head} {doc_key}#{result.old_anchor} → #{to_anchor}")
    click.echo(f"  heading: {result.old_heading!r} → {result.section.heading!r}")
    click.echo(f"  links rewritten: {result.links} in {len(result.rewritten)} section(s)")
    for ref in result.rewritten:
        click.echo(f"    - {ref}")
