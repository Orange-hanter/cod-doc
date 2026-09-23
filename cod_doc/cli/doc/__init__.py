"""CLI commands for document management.

Sub-commands live in `cmd_*.py` modules; importing them here registers
them with the `doc` click group via decorator side-effects. External
callers keep doing `from cod_doc.cli.doc import doc`.
"""

from __future__ import annotations

# Importing the cmd modules registers each click command on the `doc` group.
from . import (  # noqa: F401 — registration side-effects
    cmd_accept,
    cmd_add_section,
    cmd_backfill,
    cmd_body,
    cmd_create,
    cmd_delete,
    cmd_delete_section,
    cmd_drift,
    cmd_export,
    cmd_import,
    cmd_list,
    cmd_patch,
    cmd_rename,
    cmd_show,
    cmd_tree,
    cmd_tree_health,
)
from ._group import doc

__all__ = ["doc"]
