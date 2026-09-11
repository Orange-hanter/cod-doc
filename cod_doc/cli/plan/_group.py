"""Defines the `plan` click group."""

from __future__ import annotations

import click


@click.group()
def plan() -> None:
    """Inspect, create, and query plans (progress, ready tasks, audit, export)."""
