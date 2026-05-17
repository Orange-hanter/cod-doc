"""CLI commands for Architecture Decision Records (ADR-003)."""

from __future__ import annotations

# Importing the cmd modules registers each click command on the `adr` group.
from . import (  # noqa: F401 — registration side-effects
    cmd_export,
    cmd_graph,
    cmd_list,
    cmd_new,
    cmd_show,
    cmd_supersede,
)
from ._group import adr

__all__ = ["adr"]
