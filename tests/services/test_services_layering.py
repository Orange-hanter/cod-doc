"""ADO-041: enforce architectural rule — services layer must not import upwards.

Rule (docs/system/ARCHITECTURE.md): `cod_doc/services/*.py` sits below the
surface layers — it may use `cod_doc.core`, `cod_doc.domain`, and
`cod_doc.infra`, but never `cod_doc.mcp` / `cod_doc.api` / `cod_doc.cli` /
`cod_doc.tui`. The audit finding M6 was `agent_service` importing
`task_to_dict` from `cod_doc.mcp.tools._db`; the serializer now lives in
`cod_doc.services.serializers` and `_db` re-exports it.

Cheap structural test in the style of tests/api/test_web_layer_imports.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "cod_doc" / "services"
BANNED_PREFIXES = ("cod_doc.mcp", "cod_doc.api", "cod_doc.cli", "cod_doc.tui")


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


def test_services_layer_does_not_import_upwards() -> None:
    """No file under cod_doc/services/ may import mcp/api/cli/tui."""
    violations: list[tuple[Path, str]] = []
    for py_file in SERVICES_DIR.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        for module in _imported_modules(py_file):
            if any(module.startswith(p) for p in BANNED_PREFIXES):
                violations.append((py_file.relative_to(REPO_ROOT), module))
    assert violations == [], (
        "services layer must not import surface layers (mcp/api/cli/tui) — "
        f"move shared code down instead. Violations: {violations}"
    )
