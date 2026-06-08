"""OBI-030: RepoIndex — file/symbols/imports scanner.

Walks a project root, respects ``.gitignore`` (via ``pathspec``), and for
Python files extracts module-level symbols + imports using ``ast``.
Non-Python files get only metadata (path, size, sha1, language inferred
from extension). Designed to stay under 5s on a 1000-file repo.

Public entry:
- ``scan_project(session, project_id, repo_path)`` — full reindex.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from cod_doc.infra.models import (
    RepoFileModel,
    RepoImportModel,
    RepoSymbolModel,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ----------------------------------------------------------------- #
# Language detection                                                  #
# ----------------------------------------------------------------- #


_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".vue": "vue",
    ".svelte": "svelte",
    ".md": "markdown",
}


def _detect_language(path: Path) -> str | None:
    return _LANG_BY_EXT.get(path.suffix.lower())


# ----------------------------------------------------------------- #
# .gitignore support                                                  #
# ----------------------------------------------------------------- #


def _load_gitignore_spec(repo_root: Path):  # type: ignore[no-untyped-def]
    """Load ``.gitignore`` patterns into a pathspec matcher.

    Falls back to a permissive baseline if pathspec / .gitignore are
    unavailable. We additionally skip the standard noise dirs (`.git`,
    `node_modules`, `__pycache__`, virtual envs, build artifacts).
    """
    import pathspec

    patterns: list[str] = [
        ".git/",
        "__pycache__/",
        ".venv/",
        "venv/",
        "env/",
        ".mypy_cache/",
        ".pytest_cache/",
        ".ruff_cache/",
        ".hypothesis/",
        "node_modules/",
        "dist/",
        "build/",
        "*.egg-info/",
        ".cod-doc/",  # don't index our own state.db etc.
    ]
    gitignore = repo_root / ".gitignore"
    if gitignore.exists():
        with contextlib.suppress(OSError):
            patterns.extend(gitignore.read_text(encoding="utf-8").splitlines())
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


# ----------------------------------------------------------------- #
# Python AST extraction                                              #
# ----------------------------------------------------------------- #


def _extract_python(
    source: str,
) -> tuple[list[tuple[str, str, int, str | None]], list[tuple[str, int]]]:
    """Return ``([(name, kind, line, parent_name?), ...], [(module, line), ...])``.

    Tolerant of syntax errors — returns empty lists on parse failure.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [], []

    symbols: list[tuple[str, str, int, str | None]] = []
    imports: list[tuple[str, int]] = []

    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append((node.name, "function", node.lineno, None))
        elif isinstance(node, ast.ClassDef):
            symbols.append((node.name, "class", node.lineno, None))
            # Methods one level deep — useful for `Class.method` lookups.
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    symbols.append(
                        (child.name, "method", child.lineno, node.name),
                    )
        elif isinstance(node, ast.Assign):
            # Module-level constants (UPPER_CASE convention).
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    symbols.append((target.id, "constant", node.lineno, None))

    # Imports — walk full tree so conditional imports surface too.
    for any_node in ast.walk(tree):
        if isinstance(any_node, ast.Import):
            for alias in any_node.names:
                imports.append((alias.name, any_node.lineno))
        elif isinstance(any_node, ast.ImportFrom):
            module = any_node.module or ""
            if module:
                imports.append((module, any_node.lineno))

    return symbols, imports


# ----------------------------------------------------------------- #
# Public: scan_project                                               #
# ----------------------------------------------------------------- #


def _file_sha1(path: Path) -> str:
    h = hashlib.sha1(usedforsecurity=False)
    try:
        with path.open("rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
    except OSError:
        return "0" * 40
    return h.hexdigest()


def scan_project(
    session: Session,
    *,
    project_id: int,
    repo_path: Path | str,
    max_files: int = 5000,
) -> dict[str, int]:
    """Full reindex of ``repo_path`` under ``project_id``.

    Existing rows for the project are deleted first — simpler than diff;
    sub-second on SQLite for typical project sizes. Returns counts dict
    for log/UI reporting.
    """
    repo_root = Path(repo_path).resolve()
    if not repo_root.is_dir():
        raise ValueError(f"not a directory: {repo_root}")

    spec = _load_gitignore_spec(repo_root)

    # Truncate existing index for this project (CASCADE removes symbols+imports).
    session.execute(delete(RepoFileModel).where(RepoFileModel.project_id == project_id))
    session.flush()

    files_count = 0
    symbols_count = 0
    imports_count = 0
    skipped_gitignore = 0
    now = datetime.now(UTC)

    for path in repo_root.rglob("*"):
        if files_count >= max_files:
            break
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            continue
        if spec.match_file(rel) or spec.match_file(rel + "/"):
            skipped_gitignore += 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue

        file_row = RepoFileModel(
            project_id=project_id,
            path=rel,
            language=_detect_language(path),
            size_bytes=size,
            sha1=_file_sha1(path),
            scanned_at=now,
        )
        session.add(file_row)
        session.flush()

        # Symbol / import extraction (Python only for now).
        if file_row.language == "python":
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                source = ""
            symbols, imports = _extract_python(source)
            for name, kind, line, parent in symbols:
                session.add(
                    RepoSymbolModel(
                        file_id=file_row.row_id,
                        name=name,
                        kind=kind,
                        line=line,
                        parent_name=parent,
                    )
                )
                symbols_count += 1
            for module, line in imports:
                session.add(
                    RepoImportModel(
                        file_id=file_row.row_id,
                        module=module,
                        line=line,
                    )
                )
                imports_count += 1
        files_count += 1

    session.flush()
    return {
        "files": files_count,
        "symbols": symbols_count,
        "imports": imports_count,
        "skipped_gitignore": skipped_gitignore,
    }


def list_files(
    session: Session,
    project_id: int,
    *,
    language: str | None = None,
    limit: int = 200,
) -> list[RepoFileModel]:
    stmt = select(RepoFileModel).where(RepoFileModel.project_id == project_id)
    if language is not None:
        stmt = stmt.where(RepoFileModel.language == language)
    stmt = stmt.order_by(RepoFileModel.path).limit(limit)
    return list(session.execute(stmt).scalars())


def find_symbol(
    session: Session,
    project_id: int,
    name: str,
) -> list[tuple[RepoFileModel, RepoSymbolModel]]:
    """Locate ``name`` across all indexed files."""
    rows = session.execute(
        select(RepoFileModel, RepoSymbolModel)
        .join(RepoSymbolModel, RepoSymbolModel.file_id == RepoFileModel.row_id)
        .where(
            RepoFileModel.project_id == project_id,
            RepoSymbolModel.name == name,
        )
        .order_by(RepoFileModel.path, RepoSymbolModel.line)
    ).all()
    return [(f, s) for (f, s) in rows]


def find_importers(
    session: Session,
    project_id: int,
    module: str,
) -> list[RepoFileModel]:
    """Which files import ``module``."""
    rows = list(
        session.execute(
            select(RepoFileModel)
            .join(RepoImportModel, RepoImportModel.file_id == RepoFileModel.row_id)
            .where(
                RepoFileModel.project_id == project_id,
                RepoImportModel.module == module,
            )
            .distinct()
            .order_by(RepoFileModel.path)
        ).scalars()
    )
    return rows
