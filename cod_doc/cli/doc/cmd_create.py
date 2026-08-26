"""`doc create` — create a new document record."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from cod_doc.domain.entities import DocumentType

from ._common import _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config

# ADO-015: derived from the enum. A hand-kept copy here silently lagged behind
# `DocumentType`, so the CLI could not create the very types the importer had
# just learned to store — CLI and MCP are meant to be the same interface.
_DOC_TYPE_CHOICES = [t.value for t in DocumentType]


@doc.command("create")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--key", "doc_key", required=True, help="Unique document key (e.g. 'arch/data-model')"
)
@click.option(
    "--type",
    "doc_type",
    required=True,
    type=click.Choice(_DOC_TYPE_CHOICES),
)
@click.option(
    "--status",
    required=True,
    type=click.Choice(["draft", "review", "active", "deprecated"]),
)
@click.option("--title", required=True)
@click.option("--owner", default=None)
@click.option(
    "--sensitivity",
    default="internal",
    type=click.Choice(["public", "internal", "confidential", "restricted"]),
)
@click.option("--path", default=None, help="Relative file path (default: <doc-key>.md)")
@click.option("--preamble", default="", help="Document preamble text")
@click.option("--author", default="cli", show_default=True)
@click.option("--reason", default=None)
@click.pass_context
def doc_create(
    ctx: click.Context,
    project: str,
    doc_key: str,
    doc_type: str,
    status: str,
    title: str,
    owner: str | None,
    sensitivity: str,
    path: str | None,
    preamble: str,
    author: str,
    reason: str | None,
) -> None:
    """Create a new document record."""
    from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service
    from cod_doc.services.validation import ValidationError

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    try:
        with transactional(sf) as session:
            project_id = _require_project_id(session, project)
            d = doc_service.create(
                session,
                project_id=project_id,
                doc_key=doc_key,
                type=DocumentType(doc_type),
                status=DocumentStatus(status),
                title=title,
                author=author,
                path=path,
                sensitivity=Sensitivity(sensitivity),
                owner=owner,
                preamble=preamble,
                reason=reason,
            )
    except ValidationError as exc:
        console.print(f"[red]Validation error: {exc}[/red]")
        sys.exit(1)

    console.print(f"[green]✅ Created document [bold]{d.doc_key}[/bold]: {d.title}[/green]")
