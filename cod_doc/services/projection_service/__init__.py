"""ProjectionService — markdown export / import / drift detection.

COD-023. Implements the hash-based projection pipeline described in
[ARCHITECTURE.md §4.2](../../docs/system/ARCHITECTURE.md).

Concepts:
- **Projection**: a markdown file on disk that mirrors a `Document` in the DB.
  Projections are *artefacts* — the DB is the source of truth. They are
  regenerated deterministically from the DB at any time.
- **`projection_hash`**: SHA-256 of the last exported content, stored in
  `document.projection_hash`. Used to detect three states:
  - `STALE_EXPORT`: DB content changed but file not yet re-exported.
  - `EDITED_IN_PLACE`: the on-disk file was modified after export (hash mismatch
    between file and `projection_hash`).
  - `IN_SYNC`: file matches the last export, and DB content is also unchanged.
  - `MISSING`: the projection file does not exist on disk.

Public API:
- `render_markdown` — pure: render frontmatter + body to a markdown string.
- `export_document` — write the rendered markdown, update `projection_hash`.
- `detect_drift` — compare DB / projection_hash / file hashes.
- `import_document` — apply frontmatter changes from a file back to the DB.

Caller owns the transaction.
"""

from __future__ import annotations

from ._types import DriftReport, DriftStatus, ExportResult, PathEscapeError
from .drift import detect_drift
from .export import export_document
from .import_doc import import_document
from .render import render_markdown

__all__ = [
    "DriftReport",
    "DriftStatus",
    "ExportResult",
    "PathEscapeError",
    "detect_drift",
    "export_document",
    "import_document",
    "render_markdown",
]
