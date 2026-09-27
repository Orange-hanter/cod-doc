"""Shared session/plan-id helpers + status icons + chain renderer."""

from __future__ import annotations

import json as _json
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.table import Table

from cod_doc.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

    from sqlalchemy.orm import Session, sessionmaker

    from cod_doc.config import Config
    from cod_doc.services.plan_service import ChainEntry

console = Console()
log = get_logger("cli.plan")

#: Оба написания каждого бакета — ключи рядом, а не вместо (ADO-156):
#: `DerivedStatus` печатает легаси, а `task.status` после бэкфилла —
#: каноническое, и одна таблица обслуживает обе стороны.
_STATUS_ICON = {
    "todo": "🟡",
    "pending": "🟡",
    "in_progress": "🔵",
    "in-progress": "🔵",
    "done": "🟢",
    "empty": "⬜",
}


def _make_session(project_name: str, cfg: Config) -> sessionmaker[Session]:
    from cod_doc.infra.db import db_for_entry

    entry = cfg.get_project(project_name)
    if not entry:
        console.print(f"[red]Project not found: {project_name}[/red]")
        sys.exit(1)
    factory, _engine = db_for_entry(entry)
    return factory


def _project_id(session: Session, project: str) -> int:
    """Слаг → ``project.row_id``; нет проекта в БД — сообщение и exit 1.

    Тот же резолв, что фаза 1 ``task add-dep``. Импорт ленивый: модуль
    ``cod_doc.cli.task`` тянет ``infra`` только в телах функций (ADO-179).
    """
    from cod_doc.cli.task import _require_project_id

    return _require_project_id(session, project)


def _fail(message: str) -> None:
    """Печать ошибки красным и выход без трейсбека."""
    console.print(message, style="red", markup=False)
    sys.exit(1)


def _guard() -> AbstractContextManager[None]:
    """Перевести ошибки plan-сервиса и валидации в человеческое сообщение.

    Образец — ``cli/doc/cmd_tree.py::_guard``. Ставится снаружи
    ``transactional``: исключение сначала откатывает транзакцию, потом
    превращается в сообщение и exit 1.
    """
    from cod_doc.services.plan_service import (
        PlanAlreadyExistsError,
        SectionAlreadyExistsError,
        SectionHasTasksError,
    )
    from cod_doc.services.validation import ValidationError

    @contextmanager
    def _cm() -> Iterator[None]:
        try:
            yield
        except ValidationError as exc:
            _fail(f"Validation error: {exc}")
        except (PlanAlreadyExistsError, SectionAlreadyExistsError) as exc:
            _fail(str(exc))
        except SectionHasTasksError as exc:
            _fail(f"В секции {exc.letter} задач: {exc.task_count}. Передай --reassign-to <буква>.")
        except LookupError as exc:
            # PlanNotFoundError, SectionNotFoundError и прочие «не найдено».
            _fail(str(exc))
        except ValueError as exc:
            # reason, аргументы move, цикл — текст сервиса уже для человека.
            _fail(str(exc))

    return _cm()


def _require_plan_id(session: Session, plan_scope: str) -> int:
    from cod_doc.infra.repositories import PlanRepository

    plan = PlanRepository(session).get_by_scope(plan_scope)
    if plan is None or plan.row_id is None:
        console.print(f"[red]Plan '{plan_scope}' not found.[/red]")
        sys.exit(1)
    return plan.row_id


def _render_chain(
    chain: list[ChainEntry],
    label: str,
    task_id: str,
    as_json: bool,
) -> None:
    if as_json:
        click.echo(
            _json.dumps(
                [
                    {
                        "task_id": e.task_id,
                        "title": e.title,
                        "status": e.status.value,
                        "depth": e.depth,
                    }
                    for e in chain
                ],
                indent=2,
            )
        )
        return

    console.rule(f"[bold]{label} chain — {task_id}[/bold]")
    if not chain:
        console.print("[dim]No dependencies.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Depth", justify="right", width=6)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", width=14)
    table.add_column("Title")
    for e in chain:
        icon = _STATUS_ICON.get(e.status.value, "⚪")
        table.add_row(str(e.depth), e.task_id, f"{icon} {e.status.value}", e.title)
    console.print(table)
