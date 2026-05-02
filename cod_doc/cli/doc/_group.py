"""Stand-alone module that defines the `doc` click group.

Kept apart from the package's `__init__.py` so that command modules can
import the group without triggering the package's __init__ side-effects
(which import the command modules — circular).
"""

from __future__ import annotations

import click


@click.group()
def doc() -> None:
    """Manage documents (create, rename, export, drift, import)."""
