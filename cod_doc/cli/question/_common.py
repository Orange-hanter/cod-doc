"""Shared session/project helpers, choice lists and error mapping for `question` cmds."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config

console = Console()

# Словари дублируют StrEnum'ы домена: импорт `cod_doc.domain` здесь потянул бы
# его на старте CLI; сверку держит tests/cli/test_question_cli.py.
STATUS_CHOICES = ["open", "resolved", "dropped"]
PRIORITY_CHOICES = ["critical", "high", "medium", "low"]
LINK_KIND_CHOICES = [
    "document",
    "section",
    "task",
    "adr",
    "story",
    "scenario",
    "finding",
    "code",
    "url",
]
RELATION_CHOICES = ["about", "blocks", "addressed_by", "resolved_by", "see_also"]

STATUS_ICON = {"open": "❓", "resolved": "✅", "dropped": "🗄️"}


def make_session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    factory, _engine = db_for_entry(entry)
    return factory


def require_project_id(session: Session, project_name: str) -> int:
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(f"[red]Project '{project_name}' not in DB. Run 'project add' first.[/red]")
        sys.exit(1)
    return proj.row_id


@contextmanager
def service_errors() -> Iterator[None]:
    """Service-level errors → one red line and exit 1 instead of a traceback."""
    from cod_doc.services.question_service import (
        QuestionAlreadyExistsError,
        QuestionNotFoundError,
        QuestionOptionNotFoundError,
        QuestionStateError,
    )
    from cod_doc.services.validation import ValidationError

    try:
        yield
    except QuestionNotFoundError as exc:
        console.print(f"[red]Question '{exc.args[0]}' not found.[/red]")
        sys.exit(1)
    except QuestionOptionNotFoundError as exc:
        console.print(f"[red]Option not found: {exc.args[0]}[/red]")
        sys.exit(1)
    except (QuestionAlreadyExistsError, QuestionStateError, ValidationError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


def echo_json(payload: Any) -> None:  # noqa: ANN401 — любая JSON-сериализуемая структура
    """``--json`` goes through click.echo: rich would wrap and mark up values (ADO-176)."""
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
