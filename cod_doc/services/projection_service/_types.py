"""Result/report dataclasses + drift enum + path-escape error."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class DriftStatus(StrEnum):
    IN_SYNC = "in_sync"  # file == projection_hash == DB content
    STALE_EXPORT = "stale_export"  # DB changed but not yet exported
    EDITED_IN_PLACE = "edited_in_place"  # file changed after last export
    MISSING = "missing"  # file not on disk


@dataclass(slots=True)
class ExportResult:
    document_id: int
    path: Path
    written: bool  # False = skipped (hash already matched, or dry run)
    content_hash: str  # SHA-256 of exported content
    # ADO-010: unified diff (file → projection) filled in on `dry_run`; empty
    # string means the projection already matches the file byte-for-byte.
    diff: str | None = None


@dataclass(slots=True)
class DriftReport:
    document_id: int
    status: DriftStatus
    projection_hash: str | None  # stored in DB
    db_content_hash: str  # SHA-256 of current DB content
    file_hash: str | None  # SHA-256 of on-disk file; None if MISSING


@dataclass(slots=True)
class ProjectDriftItem:
    doc_key: str
    path: str
    report: DriftReport


@dataclass(slots=True)
class ProjectDriftReport:
    project_id: int
    total_docs: int
    counts: dict[str, int]
    issues: list[ProjectDriftItem]

    @property
    def problem_count(self) -> int:
        return len(self.issues)


class FidelityBackfillAction(StrEnum):
    """What `backfill_projection_fidelity` did with one document row."""

    FILLED = "filled"  # shape recovered from the file on disk
    FILE_MISSING = "file_missing"  # nothing on disk to recover the shape from
    SKIPPED = "skipped"  # the file *is* our own last export — nothing to learn


@dataclass(slots=True)
class FidelityBackfillItem:
    doc_key: str
    path: str
    action: FidelityBackfillAction


@dataclass(slots=True)
class FidelityBackfillReport:
    project_id: int
    scanned: int  # rows with a NULL in either fidelity column
    filled: int
    file_missing: int
    skipped: int
    items: list[FidelityBackfillItem]


class ExportGuardError(RuntimeError):
    """Raised when `export_document` refuses to overwrite a file (ADO-010).

    Three situations, all overridable with `force_write=True` (CLI:
    `--force-write`) and all previewable with `dry_run=True`:

    - the on-disk file does not match anything cod-doc has written or accepted,
      so overwriting it would silently discard human edits;
    - the target project is not the checkout cod-doc itself runs from, i.e. an
      export into someone else's repository (the pilot case);
    - ADO-022: the row predates migration `0025_projection_fidelity`, so the DB
      does not remember the file's shape (`frontmatter_raw` / `title_in_body`
      are NULL) and the export would rewrite its frontmatter or invent an H1.
      Cured by `backfill_projection_fidelity`, not by `force_write`.
    """


class PathEscapeError(ValueError):
    """Raised when a document's stored path resolves outside its project root.

    This is a defense-in-depth guard. `validation.validate_doc_path` is the
    primary check on the write-path; this guard catches DB rows that were
    poisoned before the validator existed (or by any path that bypasses the
    service layer).
    """
