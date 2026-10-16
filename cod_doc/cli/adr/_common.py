"""Shared session/project helpers for `adr` cmds."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console

from cod_doc.cli._common import require_project_id as require_project_id
from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config

console = Console()
log = get_logger("cli.adr")

STATUS_ICON = {
    "proposed": "✏️",
    "accepted": "✅",
    "superseded": "🔁",
    "deprecated": "⚠️",
    "rejected": "❌",
}


def make_session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    factory, _engine = db_for_entry(entry)
    return factory
