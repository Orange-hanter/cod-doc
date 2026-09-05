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
- `import_document` — apply markdown/frontmatter changes from a tracked file
  back to the DB and accept the file hash as the current import baseline.
  Returns an `ImportReport` whose `warnings` name every frontmatter value the
  enums could not store as written (ADO-015) — never a silent substitution.
- `backfill_projection_fidelity` — ADO-022 repair for databases older than
  migration `0025_projection_fidelity`.

**Projection fidelity (`frontmatter_raw` / `title_in_body`).** These two
columns remember the *shape* of the imported file: the verbatim YAML block and
whether the source carried an `# H1`. NULL means "no shape recorded" — which is
the honest state of a `doc create`-authored document, but also the state of
every row written before migration `0025_projection_fidelity` (ADO-022). Since
the renderer treats NULL as licence to rebuild the frontmatter from
`frontmatter_json`, exporting such a legacy row rewrites the file's metadata.
`export_document` therefore refuses that write, and
`backfill_projection_fidelity` (CLI: `cod-doc doc backfill-projection`)
recovers the columns from disk.

**Pending type re-coercion (ADO-015).** The same shape of hazard one migration
later: `document.type` may hold a value an older build coerced because its
`DocumentType` had no such member (`capability`, `audit-report`, …). Widening
the enum is what *disarms* the protection that used to keep those files
verbatim, so `export_document` also refuses to rewrite a row whose
`frontmatter_json` names a now-storable type the row does not have — the exact
set migration `0026_document_type_recoercion` repairs. The cure is applying the
migration (`cod-doc project init <slug>`), not a service call.

Caller owns the transaction.
"""

from __future__ import annotations

from ._types import (
    DriftReport,
    DriftStatus,
    ExportGuardError,
    ExportResult,
    FidelityBackfillAction,
    FidelityBackfillItem,
    FidelityBackfillReport,
    PathEscapeError,
    ProjectDriftItem,
    ProjectDriftReport,
)
from .backfill import backfill_projection_fidelity
from .drift import detect_drift, detect_project_drift, normalize_repo_path
from .export import export_document
from .import_doc import import_document
from .render import render_markdown

__all__ = [
    "DriftReport",
    "DriftStatus",
    "ExportGuardError",
    "ExportResult",
    "FidelityBackfillAction",
    "FidelityBackfillItem",
    "FidelityBackfillReport",
    "PathEscapeError",
    "ProjectDriftItem",
    "ProjectDriftReport",
    "backfill_projection_fidelity",
    "detect_drift",
    "detect_project_drift",
    "export_document",
    "import_document",
    "normalize_repo_path",
    "render_markdown",
]
