"""CLI commands for document management.

Sub-commands live in `cmd_*.py` modules; importing them here registers
them with the `doc` click group via decorator side-effects. External
callers keep doing `from cod_doc.cli.doc import doc`.
"""

from __future__ import annotations

# Importing the cmd modules registers each click command on the `doc` group.
from . import (  # noqa: F401 — registration side-effects
    cmd_accept,
    cmd_backfill,
    cmd_body,
    cmd_create,
    cmd_delete,
    cmd_drift,
    cmd_export,
    cmd_import,
    cmd_list,
    cmd_rename,
    cmd_show,
)
from ._group import doc

__all__ = ["doc"]
