"""CLI commands for plan inspection and queries."""

from __future__ import annotations

# Importing the cmd modules registers each click command on the `plan` group.
from . import (  # noqa: F401 — registration side-effects
    cmd_audit,
    cmd_chain,
    cmd_create,
    cmd_critical_path,
    cmd_export,
    cmd_freeze,
    cmd_ready,
    cmd_section_create,
    cmd_sections,
    cmd_show,
)
from ._group import plan

__all__ = ["plan"]
