"""WEB-040: enforce architectural rule — web layer must not touch infra.

Rule (capabilities/web-frontend.md §7): `cod_doc/api/web/*.py` may import
from `cod_doc.services.*`, `cod_doc.api.deps`, `cod_doc.config`, and
`cod_doc.domain.entities` (enums + dataclasses), but NOT from
`cod_doc.infra.*` or ORM models.

This is enforced as a cheap structural test rather than a global ruff
banned-api rule, because the latter is per-codebase, not per-directory —
and `cod_doc.api.deps` itself legitimately uses `cod_doc.infra.db`.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "cod_doc" / "api" / "web"
ALLOWED_PREFIXES = (
    "cod_doc.services",
    "cod_doc.api.deps",
    "cod_doc.api.web",
    "cod_doc.config",
    "cod_doc.core",  # Project class is used by index page
    "cod_doc.domain.entities",
    "cod_doc.logging_config",
)
BANNED_PREFIXES = ("cod_doc.infra",)


def _imported_modules(py_file: Path) -> list[str]:
    """Return all dotted module names imported by this file."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
    return modules


def test_web_layer_does_not_import_infra() -> None:
    """No file under cod_doc/api/web/ may import cod_doc.infra.*."""
    violations: list[tuple[Path, str]] = []
    for py_file in WEB_DIR.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        for module in _imported_modules(py_file):
            if any(module.startswith(p) for p in BANNED_PREFIXES):
                violations.append((py_file, module))
    assert violations == [], (
        f"Web layer must not import infra; route through cod_doc.api.deps. Violations: {violations}"
    )


def test_web_layer_only_imports_from_cod_doc_allowed_set() -> None:
    """Internal cod_doc imports from web/ must be on the allowed list.

    External imports (fastapi, sqlalchemy, etc.) are unconstrained — only
    cross-module discipline within `cod_doc.*` is enforced here.
    """
    unexpected: list[tuple[Path, str]] = []
    for py_file in WEB_DIR.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        for module in _imported_modules(py_file):
            if not module.startswith("cod_doc"):
                continue
            if any(module.startswith(p) for p in ALLOWED_PREFIXES):
                continue
            unexpected.append((py_file, module))
    assert unexpected == [], (
        "Unexpected cod_doc.* import in web layer (extend ALLOWED_PREFIXES "
        f"if intentional): {unexpected}"
    )
