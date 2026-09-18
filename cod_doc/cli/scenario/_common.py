"""Shared session/project helpers + choice lists for `scenario` cmds."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config

console = Console()
log = get_logger("cli.scenario")

# [RFC 24 §9] — the vocabulary is the producer's, not ours.
KIND_CHOICES = ["happy_path", "error_path", "boundary_value", "invariant", "integration"]
# [RFC 24 §8] claim statuses. Coverage verdicts are deliberately absent.
STATUS_CHOICES = ["draft", "confirmed", "retired"]
LINK_KIND_CHOICES = ["task", "story", "document", "criterion"]
RELATION_CHOICES = ["verifies", "specified_in", "exercised_by"]

STATUS_ICON = {"draft": "✏️", "confirmed": "✅", "retired": "🗄️"}


def _make_session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    factory, _engine = db_for_entry(entry)
    return factory


def _require_project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(f"[red]Project '{project_name}' not in DB. Run 'project add' first.[/red]")
        sys.exit(1)
    return proj.row_id


def _project_root(project_name: str, cfg: Config) -> Path:
    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    return entry.root
