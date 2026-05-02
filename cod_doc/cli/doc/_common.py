"""Shared session/project helpers + status-icon dictionaries for `doc` cmds."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
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
    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    return Path(entry.path).expanduser().resolve()
