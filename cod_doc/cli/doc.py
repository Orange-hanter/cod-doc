"""CLI commands for document management: doc list/show/create/rename/body/export/drift/import."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.config import Config

console = Console()
log = get_logger("cli.doc")

_STATUS_ICON = {
    "draft": "✏️",
    "review": "🔍",
    "active": "✅",
    "deprecated": "🗑️",
}

_DRIFT_ICON = {
    "in_sync": "✅",
    "stale_export": "⚠️",
    "edited_in_place": "🔄",
    "missing": "❌",
}


def _make_session(project_name: str, cfg: Config):  # type: ignore[no-untyped-def]
    from pathlib import Path

    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    url = resolve_db_url(Path(entry.path))
    engine = make_engine(url)
    return make_session_factory(engine)


def _require_project_id(session, project_name: str) -> int:  # type: ignore[no-untyped-def]
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(f"[red]Project '{project_name}' not in DB. Run 'project add' first.[/red]")
        sys.exit(1)
    return proj.row_id


def _get_root_path(project_name: str, cfg: Config) -> Path:
    from pathlib import Path

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    return Path(entry.path).expanduser().resolve()


@click.group()
def doc() -> None:
    """Manage documents (create, rename, export, drift, import)."""


# ──────────────────────────────────────────────────────────────────────────────
# doc list
# ──────────────────────────────────────────────────────────────────────────────


@doc.command("list")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_list(ctx: click.Context, project: str, as_json: bool) -> None:
    """List all documents for a project."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        docs = doc_service.list_for_project(session, project_id)

    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "doc_key": d.doc_key,
                        "title": d.title,
                        "type": d.type.value,
                        "status": d.status.value,
                        "sensitivity": d.sensitivity.value,
                        "owner": d.owner,
                        "path": d.path,
                    }
                    for d in docs
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not docs:
        console.print("[dim]No documents found.[/dim]")
        return

    table = Table(title=f"Documents — {project}", show_header=True)
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Status", width=10)
    table.add_column("Type", width=16)
    table.add_column("Sensitivity", width=12)
    table.add_column("Title")
    for d in docs:
        icon = _STATUS_ICON.get(d.status.value, "⚪")
        table.add_row(
            d.doc_key,
            f"{icon} {d.status.value}",
            d.type.value,
            d.sensitivity.value,
            d.title,
        )
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# doc show
# ──────────────────────────────────────────────────────────────────────────────


@doc.command("show")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--sections", is_flag=True, default=False, help="Include section list")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_show(ctx: click.Context, doc_key: str, project: str, sections: bool, as_json: bool) -> None:
    """Show document metadata (and optionally sections)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    sec_list = []
    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        if sections and d.row_id is not None:
            sec_list = doc_service.get_sections(session, d.row_id)

    if as_json:
        data: dict[str, Any] = {
            "doc_key": d.doc_key,
            "title": d.title,
            "type": d.type.value,
            "status": d.status.value,
            "sensitivity": d.sensitivity.value,
            "source_of_truth": d.source_of_truth,
            "owner": d.owner,
            "path": d.path,
            "preamble": d.preamble,
            "frontmatter": d.frontmatter,
            "projection_hash": d.projection_hash,
            "created": d.created.isoformat() if d.created else None,
            "last_updated": d.last_updated.isoformat() if d.last_updated else None,
        }
        if sections:
            data["sections"] = [
                {"anchor": s.anchor, "heading": s.heading, "level": s.level, "position": s.position}
                for s in sec_list
            ]
        console.print(_json.dumps(data, indent=2, ensure_ascii=False))
        return

    icon = _STATUS_ICON.get(d.status.value, "⚪")
    console.rule(f"[bold cyan]{d.doc_key}[/bold cyan]")
    console.print(f"  Title:       {d.title}")
    console.print(f"  Status:      {icon} {d.status.value}")
    console.print(f"  Type:        {d.type.value}")
    console.print(f"  Sensitivity: {d.sensitivity.value}")
    console.print(f"  Owner:       {d.owner or '—'}")
    console.print(f"  Path:        {d.path}")
    console.print(f"  SoT:         {'yes' if d.source_of_truth else 'no'}")
    if d.projection_hash:
        console.print(f"  Proj hash:   {d.projection_hash[:16]}…")
    if sections and sec_list:
        console.print()
        table = Table(title="Sections", show_header=True, box=None, padding=(0, 2))
        table.add_column("Pos", justify="right", width=4)
        table.add_column("Level", width=6)
        table.add_column("Anchor", style="cyan")
        table.add_column("Heading")
        for s in sec_list:
            table.add_row(str(s.position), str(s.level), s.anchor, s.heading)
        console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# doc create
# ──────────────────────────────────────────────────────────────────────────────


@doc.command("create")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--key", "doc_key", required=True, help="Unique document key (e.g. 'arch/data-model')"
)
@click.option(
    "--type",
    "doc_type",
    required=True,
    type=click.Choice(
        [
            "module-spec",
            "module-subdoc",
            "execution-plan",
            "task-section",
            "execution-log",
            "standard",
            "architecture",
            "vision",
            "guide",
            "user-story",
            "decision",
            "open-question",
            "redirect",
        ]
    ),
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


# ──────────────────────────────────────────────────────────────────────────────
# doc rename
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# doc body
# ──────────────────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────────────────
# doc export
# ──────────────────────────────────────────────────────────────────────────────


@doc.command("export")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--force", is_flag=True, default=False, help="Re-export even if hash matches")
@click.pass_context
def doc_export(ctx: click.Context, doc_key: str, project: str, force: bool) -> None:
    """Export a document projection to disk (writes <project-root>/<doc.path>)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service, projection_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        result = projection_service.export_document(session, d.row_id, root_path=root, force=force)

    if result.written:
        console.print(f"[green]✅ Exported to {result.path}[/green]")
    else:
        console.print(f"[dim]Skipped (already in sync): {result.path}[/dim]")


# ──────────────────────────────────────────────────────────────────────────────
# doc drift
# ──────────────────────────────────────────────────────────────────────────────


@doc.command("drift")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def doc_drift(ctx: click.Context, doc_key: str, project: str, as_json: bool) -> None:
    """Detect drift between DB content, projection hash, and on-disk file."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import doc_service, projection_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        d = doc_service.get(session, project_id, doc_key)
        if d is None or d.row_id is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)
        report = projection_service.detect_drift(session, d.row_id, root_path=root)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "doc_key": doc_key,
                    "status": report.status.value,
                    "projection_hash": report.projection_hash,
                    "db_content_hash": report.db_content_hash,
                    "file_hash": report.file_hash,
                },
                indent=2,
            )
        )
        return

    icon = _DRIFT_ICON.get(report.status.value, "⚪")
    console.rule(f"[bold]Drift — {doc_key}[/bold]")
    console.print(f"  Status: {icon} {report.status.value}")
    console.print(f"  DB hash:   {report.db_content_hash[:16]}…")
    console.print(
        f"  Proj hash: {(report.projection_hash or '—')[:16]}{'…' if report.projection_hash else ''}"
    )
    console.print(
        f"  File hash: {(report.file_hash or '(missing)')[:16]}{'…' if report.file_hash else ''}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# doc import
# ──────────────────────────────────────────────────────────────────────────────


@doc.command("import")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--author", default="cli", show_default=True)
@click.pass_context
def doc_import(ctx: click.Context, file_path: str, project: str, author: str) -> None:
    """Import frontmatter changes from a projection file back into the DB."""
    from pathlib import Path

    from cod_doc.infra.db import transactional
    from cod_doc.services import projection_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)
    root = _get_root_path(project, cfg)
    path = Path(file_path).expanduser().resolve()

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        result = projection_service.import_document(
            session, project_id, path, author=author, root_path=root
        )

    if result is None:
        console.print(f"[red]No document in this project matches path: {path}[/red]")
        sys.exit(1)
    console.print(f"[green]✅ Imported {result.doc_key} from {path.name}[/green]")
