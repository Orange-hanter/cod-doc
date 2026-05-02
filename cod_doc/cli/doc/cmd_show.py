"""`doc show` — show document metadata (and optionally sections)."""

from __future__ import annotations

import json as _json
import sys
from typing import TYPE_CHECKING, Any

import click
from rich.table import Table

from ._common import _STATUS_ICON, _make_session, _require_project_id, console
from ._group import doc

if TYPE_CHECKING:
    from cod_doc.config import Config


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
