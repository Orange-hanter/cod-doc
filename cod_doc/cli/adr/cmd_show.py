"""`adr show` — single-ADR detail view with diagrams + task links."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click
from rich.panel import Panel

from ._common import STATUS_ICON, console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.command("show")
@click.option("--project", "-p", required=True, help="Project slug")
@click.argument("adr_id")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def adr_show(ctx: click.Context, project: str, adr_id: str, as_json: bool) -> None:
    """Show full details of one ADR (with diagrams + task links)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)

    with transactional(sf) as session:
        project_id = require_project_id(session, project)
        row = adr_service.get(session, project_id, adr_id)
        if row is None:
            console.print(f"[red]ADR {adr_id!r} not found in {project!r}.[/red]")
            raise SystemExit(1)
        payload = adr_service.adr_to_dict(session, row)

    if as_json:
        console.print(_json.dumps(payload, indent=2, ensure_ascii=False))
        return

    icon = STATUS_ICON.get(payload["status"], "•")
    title_line = f"[cyan]{payload['adr_id']}[/cyan] {icon} {payload['title']}"
    meta = f"status: {payload['status']}    decided: {payload['decided_at'] or '—'}    author: {payload['author']}"

    body = [title_line, f"[dim]{meta}[/dim]", ""]
    for label, key in (
        ("Context", "context"),
        ("Decision", "decision"),
        ("Alternatives", "alternatives"),
        ("Consequences", "consequences"),
    ):
        val = payload.get(key) or "—"
        body.append(f"[bold]{label}[/bold]\n{val}\n")

    diagrams = payload.get("diagrams") or []
    if diagrams:
        body.append(f"[bold]Diagrams ({len(diagrams)})[/bold]")
        for d in diagrams:
            title = f" — {d['title']}" if d.get("title") else ""
            body.append(f"  [{d['position']}]{title}")
            body.append(f"    [dim]{d['mermaid'][:120]}{'…' if len(d['mermaid']) > 120 else ''}[/dim]")
        body.append("")

    links = payload.get("task_links") or []
    if links:
        body.append(f"[bold]Task links ({len(links)})[/bold]")
        for link in links:
            body.append(f"  {link['relation']:<12} → {link['task_id']}")

    console.print(Panel("\n".join(body), expand=False))
