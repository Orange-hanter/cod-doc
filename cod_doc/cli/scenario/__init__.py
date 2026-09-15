"""CLI commands for test-scenario authoring."""

from __future__ import annotations

# Importing the cmd modules registers each click command on the `scenario` group.
from . import (  # noqa: F401 — registration side-effects
    cmd_coverage,
    cmd_export,
    cmd_link,
    cmd_list,
    cmd_new,
    cmd_retire,
    cmd_show,
    cmd_steps,
    cmd_update,
)
from ._group import scenario

__all__ = ["scenario"]
