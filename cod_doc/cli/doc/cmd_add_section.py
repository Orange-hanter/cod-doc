"""`doc add-section` — add a section to a DB-authored document (mirror of MCP `doc_add_section`)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _read_body, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


@doc.command("add-section")
@click.argument("doc_key")
@click.argument("anchor")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--heading", required=True, help="Section heading text")
@click.option("--level", default=2, show_default=True, type=int, help="Markdown heading level")
@click.option(
    "--position",
    default=None,
    type=int,
    help="Index in the document; default appends to the end",
)
@click.option(
    "--body-file",
    "body_file",
    default=None,
    help="File with the section body; '-' reads stdin",
)
@click.option("--body", default=None, help="Body inline (for one-liners)")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be added without writing anything",
)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_add_section(
    ctx: click.Context,
    doc_key: str,
    anchor: str,
    project: str,
    heading: str,
    level: int,
    position: int | None,
    body_file: str | None,
    body: str | None,
    author: str,
    reason: str | None,
    dry_run: bool,
    as_json: bool,
) -> None:
    """Add section ANCHOR to DOC_KEY.

    `doc create` stores only the preamble, so this is how a document authored in
    the DB gains a body without ever touching a markdown file. A duplicate
    anchor is rejected — change an existing section with `doc patch`.
    `--dry-run` previews the diff without opening a write at all.
    """
    from cod_doc.domain.entities import EntityKind
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services import revision_service as revisions
    from cod_doc.services.doc_service import SectionAlreadyExistsError

    new_body = _read_body(body_file, body=body)

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    revision_id: str | None = None
    try:
        with transactional(sf, commit=not dry_run) as session:
            project_id = _require_project_id(session, project)
            d = doc_service.get(session, project_id, doc_key)
            if d is None or d.row_id is None:
                console.print(f"[red]Document '{doc_key}' not found.[/red]")
                sys.exit(1)

            existing = doc_service.get_sections(session, d.row_id)
            at = (
                max((s.position for s in existing), default=-1) + 1
                if position is None
                else position
            )

            if dry_run:
                # Deliberately never call add_section here: it writes through
                # `session.begin_nested()`, and a SAVEPOINT on pysqlite is not
                # undone by the enclosing rollback (STO-022) — a "preview" built
                # on that rollback would silently create the section.
                if any(s.anchor == anchor for s in existing):
                    raise SectionAlreadyExistsError(anchor)
                content_hash = doc_service.content_hash(new_body)
            else:
                created = doc_service.add_section(
                    session,
                    document_id=d.row_id,
                    anchor=anchor,
                    heading=heading,
                    level=level,
                    position=at,
                    body=new_body,
                    author=author,
                    reason=reason,
                )
                assert created.row_id is not None
                revision_id = revisions.head_for_entity(session, EntityKind.SECTION, created.row_id)
                content_hash = created.content_hash
    except SectionAlreadyExistsError:
        console.print(
            f"[red]Section '{anchor}' already exists in '{doc_key}' — "
            f"use 'doc patch' to change it.[/red]"
        )
        sys.exit(1)

    if as_json:
        payload: dict[str, object] = {
            "doc_key": doc_key,
            "anchor": anchor,
            "heading": heading,
            "level": level,
            "position": at,
            "revision_id": revision_id,
            "content_hash": content_hash,
            "created": not dry_run,
            "dry_run": dry_run,
        }
        if dry_run:
            payload["diff"] = doc_service.section_create_diff(
                new_body, doc_key=doc_key, anchor=anchor
            )
        click.echo(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    if dry_run:
        console.print(f"[cyan]🔍 Dry run {doc_key}#{anchor}[/cyan]")
        # click.echo, not console.print: a diff is raw text and may contain
        # square brackets that rich would swallow as markup.
        click.echo(doc_service.section_create_diff(new_body, doc_key=doc_key, anchor=anchor))
        return
    console.print(
        f"[green]✅ Added {doc_key}#{anchor}[/green]\n"
        f"[dim]position {at}, revision {revision_id}[/dim]"
    )
