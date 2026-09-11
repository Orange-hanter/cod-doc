"""MCP tools: hash.* — MASTER.md hybrid-ref registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.mcp.tools._db import session_factory

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register hash.* tools on the given FastMCP instance."""

    @mcp.tool(name="hash_update")
    def hash_update(
        project: str, master_path: str | None = None
    ) -> dict[str, int | list[str] | str]:
        """Rewrite hybrid-ref hashes in MASTER.md (``📁 … | 🗃️ … | 🔑 sha:``).

        Does not rewrite the §5.1 table. Does not import the file into the DB —
        call ``doc_import`` on MASTER.md afterwards if the document is registered.
        File-only: no DB transaction, no activity event.
        """
        from pathlib import Path

        from cod_doc.core.hash_calc import update_hashes

        _, entry = session_factory(project)
        root = Path(entry.path).expanduser().resolve()
        target = Path(master_path).expanduser() if master_path else Path("MASTER.md")
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        updated, warnings = update_hashes(target)
        return {"updated": updated, "warnings": warnings, "path": str(target)}
