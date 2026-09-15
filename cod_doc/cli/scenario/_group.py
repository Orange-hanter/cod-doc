"""Defines the `scenario` click group; isolated to avoid circular imports."""

from __future__ import annotations

import click


@click.group()
def scenario() -> None:
    """Author test scenarios and project them into docs/system/scenarios/."""
