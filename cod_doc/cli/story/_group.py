"""Defines the `story` click group; isolated to avoid circular imports."""

from __future__ import annotations

import click


@click.group()
def story() -> None:
    """Manage user stories (create, status, link, coverage)."""
