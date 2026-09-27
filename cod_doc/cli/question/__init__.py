"""CLI commands for open questions."""

from __future__ import annotations

# Importing the cmd modules registers each click command on the `question` group.
from . import (  # noqa: F401 — registration side-effects
    cmd_crud,
    cmd_import,
    cmd_lifecycle,
    cmd_link,
    cmd_option,
)
from ._group import question

__all__ = ["question"]
