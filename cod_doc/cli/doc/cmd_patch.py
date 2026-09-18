"""`doc patch` — replace a section body in the DB (mirror of MCP `doc_patch_section`)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _read_body, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("patch")
@click.argument("doc_key")
@click.argument("anchor")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--body-file",
    "body_file",
    default=None,
    help="File with the new section body; '-' reads stdin",
)
@click.option("--body", default=None, help="New body inline (for one-liners)")
@click.option(
    "--expected-revision",
    "expected_revision",
    default=None,
    help="Revision id you last saw; a newer head aborts the write instead of overwriting it",
)
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show the unified diff without writing anything",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_patch(
    ctx: click.Context,
    doc_key: str,
    anchor: str,
    project: str,
    body_file: str | None,
    body: str | None,
    expected_revision: str | None,
    author: str,
    reason: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Replace the body of section ANCHOR in DOC_KEY.

    The body comes from a file or stdin, never from a shell argument — section
    bodies are multi-line markdown. Pass `--expected-revision` with the revision
    id you last observed and a concurrent writer landing first aborts the write
    instead of silently overwriting it.
    """
    from cod_doc.domain.entities import EntityKind
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services import revision_service as revisions
    from cod_doc.services.revision_service import NO_PARENT_CHECK, RevisionConflictError

    if body_file is None and body is None:
        console.print("[red]Pass --body-file <path>|- or --body <text>.[/red]")
        sys.exit(1)
    new_body = _read_body(body_file, body=body)

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    expected: str | object = NO_PARENT_CHECK if expected_revision is None else expected_revision
    try:
        with transactional(sf, commit=not dry_run) as session:
            project_id = _require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                console.print(f"[red]Document '{doc_key}' not found.[/red]")
                sys.exit(1)

            section = next(
                (s for s in doc_service.get_sections(session, d.row_id) if s.anchor == anchor),
                None,
            )
            if section is None:
                console.print(f"[red]Section '{anchor}' not found in document '{doc_key}'.[/red]")
                sys.exit(1)
            old_body = section.body

            updated = doc_service.patch_section(
                session,
                document_id=d.row_id,
                anchor=anchor,
                new_body=new_body,
                author=author,
                reason=reason,
                expected_parent_revision_id=expected,
            )
            assert updated.row_id is not None
            revision_id = revisions.head_for_entity(session, EntityKind.SECTION, updated.row_id)
            content_hash = updated.content_hash
    except RevisionConflictError as exc:
        console.print(f"[red]Conflict: {exc}[/red]")
        sys.exit(1)

    changed = old_body != new_body
    diff = (
        doc_service.section_diff(old_body, new_body, doc_key=doc_key, anchor=anchor)
        if changed
        else ""
    )

    if as_json:
        payload = {
            "doc_key": doc_key,
            "anchor": anchor,
            "revision_id": revision_id,
            "changed": changed,
            "content_hash": content_hash,
            "dry_run": dry_run,
        }
        if dry_run:
            payload["diff"] = diff
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if not changed:
        console.print(f"[yellow]⚪ Unchanged {doc_key}#{anchor}[/yellow]")
        console.print("[dim]body identical — no revision written[/dim]")
        return
    if dry_run:
        console.print(f"[cyan]🔍 Dry run {doc_key}#{anchor}[/cyan]")
        # click.echo, not console.print: a diff is raw text and may contain
        # square brackets that rich would swallow as markup.
        click.echo(diff)
        return
    console.print(
        f"[green]✅ Patched {doc_key}#{anchor}[/green]\n[dim]revision {revision_id}[/dim]"
    )
