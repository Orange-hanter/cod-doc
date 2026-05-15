"""Defines the `adr` click group; isolated to avoid circular imports."""

from __future__ import annotations

import click


@click.group()
def adr() -> None:
    """Manage Architecture Decision Records (new / list / show / supersede / graph)."""
