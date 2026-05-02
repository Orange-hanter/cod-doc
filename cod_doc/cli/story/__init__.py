"""CLI commands for user-story management."""

from __future__ import annotations

# Importing the cmd modules registers each click command on the `story` group.
from . import (  # noqa: F401 — registration side-effects
    cmd_add_criterion,
    cmd_coverage,
    cmd_create,
    cmd_link,
    cmd_list,
    cmd_show,
    cmd_status,
)
from ._group import story

__all__ = ["story"]
