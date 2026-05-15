"""`adr graph` — emit the supersede DAG as JSON or a Mermaid block."""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING

import click

from ._common import STATUS_ICON, console, make_session, require_project_id
from ._group import adr

if TYPE_CHECKING:
    from cod_doc.config import Config


@adr.command("graph")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["mermaid", "json"]),
    default="mermaid",
    show_default=True,
)
@click.pass_context
def adr_graph(ctx: click.Context, project: str, fmt: str) -> None:
    """Emit the full ADR supersede DAG (Mermaid markdown or JSON)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import adr_service

    cfg: Config = ctx.obj["config"]
    sf = make_session(project, cfg)

    with transactional(sf) as session:
        project_id = require_project_id(session, project)
        graph = adr_service.graph(session, project_id)

    if fmt == "json":
        console.print(_json.dumps(graph, indent=2, ensure_ascii=False))
        return

    # Mermaid block.
    lines = ["```mermaid", "graph LR"]
    for node in graph["nodes"]:
        icon = STATUS_ICON.get(node["status"], "•")
        node_id = node["adr_id"].replace("-", "_")
        label = f"{icon} {node['adr_id']}<br/>{node['title']}"
        lines.append(f"  {node_id}[\"{label}\"]")
    for edge in graph["edges"]:
        from_id = edge["from"].replace("-", "_")
        to_id = edge["to"].replace("-", "_")
        label = f"|{edge['reason'][:30]}|" if edge.get("reason") else ""
        lines.append(f"  {from_id} -->{label} {to_id}")
    lines.append("```")
    console.print("\n".join(lines))
