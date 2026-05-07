"""CLI commands for link management: link list/sync/verify."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from cod_doc.config import Config

console = Console()
log = get_logger("cli.link")


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


def _resolve_section_id(session, project_id: int, doc_key: str, anchor: str) -> int:  # type: ignore[no-untyped-def]
    """Look up section row_id by (project_id, doc_key, anchor); exit on failure."""
    from sqlalchemy import select

    from cod_doc.infra.models import DocumentModel, SectionModel

    doc_row = session.execute(
        select(DocumentModel.row_id).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()
    if doc_row is None:
        console.print(f"[red]Document '{doc_key}' not found.[/red]")
        sys.exit(1)

    sec_row = session.execute(
        select(SectionModel.row_id).where(
            SectionModel.document_id == doc_row,
            SectionModel.anchor == anchor,
        )
    ).scalar_one_or_none()
    if sec_row is None:
        console.print(f"[red]Section '{doc_key}#{anchor}' not found.[/red]")
        sys.exit(1)
    return int(sec_row)


@click.group()
def link() -> None:
    """Manage document links (list, sync, verify)."""


# ──────────────────────────────────────────────────────────────────────────────
# link list
# ──────────────────────────────────────────────────────────────────────────────


@link.command("list")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--section", "anchor", default=None, help="Restrict to one section anchor")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def link_list(
    ctx: click.Context, doc_key: str, project: str, anchor: str | None, as_json: bool
) -> None:
    """List links in a document (or a single section with --section)."""
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import DocumentModel, SectionModel
    from cod_doc.services import link_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    links = []
    with transactional(sf) as session:
        project_id = _require_project_id(session, project)

        doc_row = session.execute(
            select(DocumentModel.row_id).where(
                DocumentModel.project_id == project_id,
                DocumentModel.doc_key == doc_key,
            )
        ).scalar_one_or_none()
        if doc_row is None:
            console.print(f"[red]Document '{doc_key}' not found.[/red]")
            sys.exit(1)

        if anchor:
            sec_id = _resolve_section_id(session, project_id, doc_key, anchor)
            links = link_service.list_for_section(session, sec_id)
        else:
            sec_ids = (
                session.execute(
                    select(SectionModel.row_id).where(SectionModel.document_id == doc_row)
                )
                .scalars()
                .all()
            )
            for sid in sec_ids:
                links.extend(link_service.list_for_section(session, sid))

    if as_json:
        console.print(
            _json.dumps(
                [
                    {
                        "raw": lk.raw,
                        "kind": lk.kind.value,
                        "resolved": lk.resolved,
                        "to_doc_key": lk.to_doc_key,
                        "to_task_id": lk.to_task_id,
                        "to_story_id": lk.to_story_id,
                        "broken_reason": lk.broken_reason,
                    }
                    for lk in links
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if not links:
        console.print("[dim]No links found.[/dim]")
        return

    table = Table(title=f"Links — {doc_key}{f'#{anchor}' if anchor else ''}", show_header=True)
    table.add_column("Kind", width=10)
    table.add_column("Resolved", width=10)
    table.add_column("Target / Raw")
    table.add_column("Broken reason")
    for lk in links:
        resolved_icon = "✅" if lk.resolved else "❌"
        target = lk.to_doc_key or lk.to_task_id or lk.to_story_id or lk.raw[:60]
        table.add_row(
            lk.kind.value,
            resolved_icon,
            target,
            lk.broken_reason or "",
        )
    console.print(table)


# ──────────────────────────────────────────────────────────────────────────────
# link sync
# ──────────────────────────────────────────────────────────────────────────────


@link.command("sync")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--section", "anchor", required=True, help="Section anchor")
@click.pass_context
def link_sync(ctx: click.Context, doc_key: str, project: str, anchor: str) -> None:
    """Re-parse and sync link rows for a section from its body."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import link_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        sec_id = _resolve_section_id(session, project_id, doc_key, anchor)
        links = link_service.sync_section(session, sec_id)

    console.print(f"[green]✅ Synced {len(links)} link(s) for {doc_key}#{anchor}[/green]")


# ──────────────────────────────────────────────────────────────────────────────
# link verify
# ──────────────────────────────────────────────────────────────────────────────


@link.command("backfill")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be synced without persisting.",
)
@click.pass_context
def link_backfill(ctx: click.Context, project: str, dry_run: bool) -> None:
    """COD-079: re-parse links for every section in the project.

    Existing imports skipped link extraction; this walks all sections and
    runs ``sync_section`` so the in-memory + UI link panels show real data.
    """
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import DocumentModel, SectionModel
    from cod_doc.services import link_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    sections_done = 0
    links_total = 0
    docs_seen: set[str] = set()
    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        rows = (
            session.execute(
                select(SectionModel.row_id, DocumentModel.doc_key)
                .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
                .where(DocumentModel.project_id == project_id)
                .order_by(DocumentModel.doc_key, SectionModel.position)
            )
            .all()
        )
        for sec_id, doc_key in rows:
            try:
                links = link_service.sync_section(session, int(sec_id))
            except Exception as exc:
                log.warning("backfill skipped %s: %s", doc_key, exc)
                continue
            sections_done += 1
            links_total += len(links)
            docs_seen.add(doc_key)
        if dry_run:
            session.rollback()

    note = "(dry-run, rolled back)" if dry_run else ""
    console.print(
        f"[green]✅[/green] backfilled {links_total} link(s) across "
        f"{sections_done} section(s) in {len(docs_seen)} doc(s) {note}".strip()
    )


@link.command("verify")
@click.argument("doc_key")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--section", "anchor", required=True, help="Section anchor")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def link_verify(ctx: click.Context, doc_key: str, project: str, anchor: str, as_json: bool) -> None:
    """Verify link resolution for a section; exit 1 if any are broken."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import link_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        sec_id = _resolve_section_id(session, project_id, doc_key, anchor)
        report = link_service.verify_section(session, sec_id)

    if as_json:
        console.print(
            _json.dumps(
                {
                    "doc_key": doc_key,
                    "anchor": anchor,
                    "ok": report.ok,
                    "broken": report.broken,
                    "skipped": report.skipped,
                },
                indent=2,
            )
        )
        if report.broken:
            sys.exit(1)
        return

    status_icon = "✅" if report.broken == 0 else "❌"
    console.print(
        f"{status_icon} {doc_key}#{anchor}: "
        f"[green]{report.ok} ok[/green], "
        f"[red]{report.broken} broken[/red], "
        f"[dim]{report.skipped} skipped (URL)[/dim]"
    )
    if report.broken:
        sys.exit(1)
