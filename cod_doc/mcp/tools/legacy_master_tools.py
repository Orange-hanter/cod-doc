"""MASTER.md hash + reference + raw filesystem context delivery tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.core.context import get_context
from cod_doc.core.hash_calc import calc_hash, check_hash, make_ref, update_hashes

from ._legacy import open_project

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register MASTER.md, hash, and context-delivery tools."""

    @mcp.tool()
    def get_master(project_name: str) -> str:
        """Return raw MASTER.md content for a project."""
        proj = open_project(project_name)
        content = proj.read_master()
        if content is None:
            raise ValueError(f"MASTER.md не найден для проекта: {project_name}")
        return content

    @mcp.tool()
    def update_master_hashes(project_name: str) -> dict[str, Any]:
        """Recalculate all SHA-256 hashes in MASTER.md hybrid references."""
        proj = open_project(project_name)
        updated, warnings = update_hashes(proj.entry.master_path)
        return {"updated": updated, "warnings": warnings}

    @mcp.tool()
    def check_stale_refs(project_name: str) -> dict[str, Any]:
        """Scan MASTER.md for hybrid references; flag stale/missing files."""
        from cod_doc.core.hash_calc import LINK_PATTERN

        proj = open_project(project_name)
        content = proj.read_master() or ""
        repo_root = proj.entry.root

        results: list[dict[str, str]] = []
        for m in LINK_PATTERN.finditer(content):
            rel = m.group("path").lstrip("/")
            expected = m.group("hash")
            target = repo_root / rel
            if not target.exists():
                results.append({"path": rel, "status": "BROKEN", "expected": expected})
            elif not check_hash(target, expected):
                actual = calc_hash(target)
                results.append(
                    {"path": rel, "status": "STALE", "expected": expected, "actual": actual}
                )
            else:
                results.append({"path": rel, "status": "VALID", "hash": expected})

        stale = sum(1 for r in results if r["status"] == "STALE")
        broken = sum(1 for r in results if r["status"] == "BROKEN")
        return {
            "refs": results,
            "summary": {
                "total": len(results),
                "valid": len(results) - stale - broken,
                "stale": stale,
                "broken": broken,
            },
        }

    @mcp.tool()
    def generate_ref(project_name: str, file_path: str) -> str:
        """Generate a hybrid reference for a file relative to the project root."""
        proj = open_project(project_name)
        target = proj.entry.root / file_path
        if not target.exists():
            raise ValueError(f"Файл не найден: {file_path}")
        return make_ref(target, proj.entry.root)

    @mcp.tool()
    def read_context(
        project_name: str,
        ref: str,
        depth: str = "L1",
        page: int = 1,
    ) -> dict[str, Any]:
        """Read file content by hybrid reference with hash validation."""
        proj = open_project(project_name)
        return get_context(ref, proj.entry.root, depth=depth, page=page)

    @mcp.tool()
    def read_file(
        project_name: str,
        file_path: str,
        page: int = 1,
    ) -> dict[str, Any]:
        """Read file content by relative path (no hash validation)."""
        proj = open_project(project_name)
        target = proj.entry.root / file_path
        if not target.exists():
            raise ValueError(f"Файл не найден: {file_path}")

        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
        page_size = 200
        total_pages = max(1, (len(lines) + page_size - 1) // page_size)
        start = (page - 1) * page_size
        content = "".join(lines[start : start + page_size])

        return {
            "path": file_path,
            "content": content,
            "total_lines": len(lines),
            "page": page,
            "total_pages": total_pages,
            "has_more": page < total_pages,
        }

    @mcp.tool()
    def list_files(
        project_name: str,
        directory: str = ".",
        pattern: str = "*",
    ) -> list[str]:
        """List files in a project directory matching a glob pattern."""
        proj = open_project(project_name)
        target = proj.entry.root / directory
        if not target.exists():
            raise ValueError(f"Директория не найдена: {directory}")
        return sorted(
            str(f.relative_to(proj.entry.root))
            for f in target.rglob(pattern)
            if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts
        )

    @mcp.tool()
    def hash_file(project_name: str, file_path: str) -> dict[str, str]:
        """Compute SHA-256 hash (first 12 hex chars) for a project file."""
        proj = open_project(project_name)
        target = proj.entry.root / file_path
        if not target.exists():
            raise ValueError(f"Файл не найден: {file_path}")
        return {"path": file_path, "hash": calc_hash(target)}

    @mcp.tool()
    def verify_hash(project_name: str, file_path: str, expected_hash: str) -> dict[str, Any]:
        """Check if a file's current hash matches the expected value."""
        proj = open_project(project_name)
        target = proj.entry.root / file_path
        if not target.exists():
            raise ValueError(f"Файл не найден: {file_path}")
        actual = calc_hash(target)
        return {
            "path": file_path,
            "expected": expected_hash,
            "actual": actual,
            "valid": check_hash(target, expected_hash),
        }
