"""MASTER.md hash + reference + raw filesystem context delivery tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.core.context import get_context
from cod_doc.core.hash_calc import calc_hash, check_hash, make_ref, update_hashes

from ._legacy import open_project, resolve_project_name

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register MASTER.md, hash, and context-delivery tools."""

    @mcp.tool()
    def get_master(
        project: str | None = None,
        project_name: str | None = None,
    ) -> str:
        """Return raw MASTER.md content for a project.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias);
        passing the legacy form emits a DeprecationWarning. See PCA-934.
        """
        name = resolve_project_name(project, project_name, "get_master")
        proj = open_project(name)
        content = proj.read_master()
        if content is None:
            raise ValueError(f"MASTER.md не найден для проекта: {name}")
        return content

    @mcp.tool()
    def update_master_hashes(
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Recalculate all SHA-256 hashes in MASTER.md hybrid references.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "update_master_hashes")
        proj = open_project(name)
        updated, warnings = update_hashes(proj.entry.master_path)
        return {"updated": updated, "warnings": warnings}

    @mcp.tool()
    def check_stale_refs(
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Scan MASTER.md for hybrid references; flag stale/missing files.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        from cod_doc.core.hash_calc import LINK_PATTERN

        name = resolve_project_name(project, project_name, "check_stale_refs")
        proj = open_project(name)
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
    def generate_ref(
        file_path: str,
        project: str | None = None,
        project_name: str | None = None,
    ) -> str:
        """Generate a hybrid reference for a file relative to the project root.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "generate_ref")
        proj = open_project(name)
        target = proj.entry.root / file_path
        if not target.exists():
            raise ValueError(f"Файл не найден: {file_path}")
        return make_ref(target, proj.entry.root)

    @mcp.tool()
    def read_context(
        ref: str,
        depth: str = "L1",
        page: int = 1,
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Read file content by hybrid reference with hash validation.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "read_context")
        proj = open_project(name)
        return get_context(ref, proj.entry.root, depth=depth, page=page)

    @mcp.tool()
    def read_file(
        file_path: str,
        page: int = 1,
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Read file content by relative path (no hash validation).

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "read_file")
        proj = open_project(name)
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
        directory: str = ".",
        pattern: str = "*",
        project: str | None = None,
        project_name: str | None = None,
    ) -> list[str]:
        """List files in a project directory matching a glob pattern.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "list_files")
        proj = open_project(name)
        target = proj.entry.root / directory
        if not target.exists():
            raise ValueError(f"Директория не найдена: {directory}")
        return sorted(
            str(f.relative_to(proj.entry.root))
            for f in target.rglob(pattern)
            if f.is_file() and ".git" not in f.parts and "node_modules" not in f.parts
        )

    @mcp.tool()
    def hash_file(
        file_path: str,
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, str]:
        """Compute SHA-256 hash (first 12 hex chars) for a project file.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "hash_file")
        proj = open_project(name)
        target = proj.entry.root / file_path
        if not target.exists():
            raise ValueError(f"Файл не найден: {file_path}")
        return {"path": file_path, "hash": calc_hash(target)}

    @mcp.tool()
    def verify_hash(
        file_path: str,
        expected_hash: str,
        project: str | None = None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        """Check if a file's current hash matches the expected value.

        Accepts ``project`` (canonical) or ``project_name`` (legacy alias).
        """
        name = resolve_project_name(project, project_name, "verify_hash")
        proj = open_project(name)
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
