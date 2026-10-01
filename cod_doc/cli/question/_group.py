"""Defines the `question` click group; isolated to avoid circular imports."""

from __future__ import annotations

import click


@click.group()
def question() -> None:
    """Open questions — DB entity with options and links; never projected to markdown."""
