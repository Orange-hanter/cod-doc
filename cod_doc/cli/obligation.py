"""CLI: ``cod-doc obligation export``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from cod_doc.services.structure_obligations import export_obligations, generate_property_drafts
from cod_doc.services.structure_protocol import as_list

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config


def _session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import make_engine, make_session_factory, resolve_db_url

    entry = cfg.get_project(project_name)
    if not entry:
        click.echo(f"Project not found: {project_name}", err=True)
        sys.exit(1)
    return make_session_factory(make_engine(resolve_db_url(Path(entry.path))))


@click.group()
def obligation() -> None:
    """Documented obligations for the structure protocol."""


@obligation.command("export")
@click.option("--project", "-p", required=True)
@click.option("--head-sha", default="unknown")
@click.option("--json", "as_json", is_flag=True, default=True)
@click.option("--with-property-drafts", is_flag=True, default=False)
@click.pass_context
def obligation_export(
    ctx: click.Context,
    project: str,
    head_sha: str,
    as_json: bool,
    with_property_drafts: bool,
) -> None:
    """Export versioned obligations_export.v1 from specs, stories and claims."""
    from cod_doc.infra.db import transactional
    from cod_doc.infra.repositories import ProjectRepository

    cfg: Config = ctx.obj["config"]
    with transactional(_session(project, cfg), commit=False) as session:
        proj = ProjectRepository(session).get_by_slug(project)
        if proj is None or proj.row_id is None:
            click.echo(f"Project '{project}' not in DB.", err=True)
            sys.exit(1)
        payload = export_obligations(session, proj.row_id, project_slug=project, head_sha=head_sha)
        if with_property_drafts:
            payload["propertyDrafts"] = generate_property_drafts(
                as_list(payload.get("obligations"), label="obligations")
            )
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    if not as_json:
        click.echo(f"obligations={len(as_list(payload.get('obligations'), label='obligations'))}")
