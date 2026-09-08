"""`scenario show` — full text of one scenario."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import click

from ._common import _make_session, _require_project_id, console
from ._group import scenario

if TYPE_CHECKING:
    from cod_doc.config import Config


@scenario.command("show")
@click.argument("scenario_id")
@click.option("--project", "-p", required=True, help="Project slug")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.pass_context
def scenario_show(
    ctx: click.Context,
    scenario_id: str,
    project: str,
    as_json: bool,
) -> None:
    """Show one scenario with its steps and links."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import scenario_service

    cfg: Config = ctx.obj["config"]
    sf = _make_session(project, cfg)

    with transactional(sf) as session:
        project_id = _require_project_id(session, project)
        found = scenario_service.get(session, project_id, scenario_id)
        if found is None or found.row_id is None:
            console.print(f"[red]Scenario '{scenario_id}' not found.[/red]")
            sys.exit(1)
        steps = [st.text for st in scenario_service.list_steps(session, found.row_id)]
        links = [
            (link.relation.value, link.to_kind.value, link.to_ref)
            for link in scenario_service.list_links(session, found.row_id)
        ]
        anchor_suffix = f"#{found.section_anchor}" if found.section_anchor else ""
        payload = {
            "scenario_id": found.scenario_id,
            "title": found.title,
            "kind": found.kind.value,
            "status": found.status.value,
            "group_key": found.group_key,
            "doc_key": found.doc_key,
            "section_anchor": found.section_anchor,
            "preconditions": found.preconditions,
            "expected": found.expected,
            "notes": found.notes,
            "steps": steps,
            "links": [
                {"relation": relation, "kind": kind, "ref": ref} for relation, kind, ref in links
            ],
        }
        title = found.title
        kind = found.kind.value
        status = found.status.value
        group_key = found.group_key
        doc_key = found.doc_key
        preconditions = found.preconditions
        expected = found.expected
        notes = found.notes

    if as_json:
        console.print_json(json.dumps(payload))
        return

    console.print(f"[bold]{scenario_id} — {title}[/bold]")
    console.print(f"kind: {kind}   status: {status}")
    console.print(f"group: {group_key}")
    if doc_key:
        console.print(f"anchor: {doc_key}{anchor_suffix}")
    console.print("\n[bold]Preconditions[/bold]")
    console.print(preconditions)
    console.print("\n[bold]Steps[/bold]")
    for i, step in enumerate(steps, start=1):
        console.print(f"  {i}. {step}")
    console.print("\n[bold]Expected result[/bold]")
    console.print(expected)
    if notes:
        console.print("\n[bold]Notes[/bold]")
        console.print(notes)
    if links:
        console.print("\n[bold]Links[/bold]")
        for relation, link_kind, ref in links:
            console.print(f"  {relation} → {link_kind}:{ref}")
