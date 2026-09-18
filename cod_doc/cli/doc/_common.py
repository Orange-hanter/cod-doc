"""Shared session/project helpers + status-icon dictionaries for `doc` cmds."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

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


def _read_body(body_file: str | None, *, body: str | None = None) -> str:
    """Resolve a section body from `--body-file <path>`, `--body-file -` or `--body`.

    A section body is multi-line markdown; typing it as a shell argument is
    unusable, so the file/stdin form is the primary one (STO-011). Exactly one
    source may be given.
    """
    if body_file is not None and body is not None:
        console.print("[red]Use either --body-file or --body, not both.[/red]")
        sys.exit(1)
    if body is not None:
        return body
    if body_file is None:
        return ""
    if body_file == "-":
        return sys.stdin.read()
    path = Path(body_file).expanduser()
    if not path.is_file():
        console.print(f"[red]Body file not found: {body_file}[/red]")
        sys.exit(1)
    return path.read_text(encoding="utf-8")


def _get_root_path(project_name: str, cfg: Config) -> Path:
    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    return Path(entry.path).expanduser().resolve()
