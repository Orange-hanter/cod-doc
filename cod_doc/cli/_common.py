"""Помощники, общие для нескольких групп CLI.

Модуль импортируется на старте CLI из ``_common`` групп, поэтому тянет только
stdlib и rich; ``infra``/``services`` — в телах функций (ADO-179,
``tests/cli/test_cli_startup_is_light.py``).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

console = Console()


def printable(text: str) -> str:
    """Экранировать непечатаемые символы пользовательского ввода для терминала.

    ``markup=False`` гасит rich-разметку, но не ANSI-последовательности:
    слаг с ``\\x1b[2J`` очистил бы экран, ``\\x1b]0;…\\x07`` переименовал бы
    окно. Непечатаемый символ выводится escape-записью (``\\x1b``, ``\\n``),
    кириллица и прочие печатаемые символы остаются как есть.
    """
    return "".join(
        ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii") for ch in text
    )


def require_project_id(session: Session, project_name: str) -> int:
    """Слаг → ``project.row_id``; нет проекта в БД — сообщение и exit 1.

    В пишущей транзакции за ``_guard`` вместо него зовут
    ``services.project_lookup.require_project_id``: ``ProjectNotFoundError`` —
    ``LookupError``, и ``_guard`` печатает её после отката.
    """
    from cod_doc.infra.repositories import ProjectRepository

    proj = ProjectRepository(session).get_by_slug(project_name)
    if proj is None or proj.row_id is None:
        console.print(
            f"Project '{printable(project_name)}' not in DB. Run 'project add' first.",
            style="red",
            markup=False,
        )
        sys.exit(1)
    return proj.row_id
