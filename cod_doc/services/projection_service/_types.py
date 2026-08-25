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


class ExportGuardError(RuntimeError):
    """Raised when `export_document` refuses to overwrite a file (ADO-010).

    Two situations, both overridable with `force_write=True` (CLI:
    `--force-write`) and both previewable with `dry_run=True`:

    - the on-disk file does not match anything cod-doc has written or accepted,
      so overwriting it would silently discard human edits;
    - the target project is not the checkout cod-doc itself runs from, i.e. an
      export into someone else's repository (the pilot case).
    """


class PathEscapeError(ValueError):
    """Raised when a document's stored path resolves outside its project root.

    This is a defense-in-depth guard. `validation.validate_doc_path` is the
    primary check on the write-path; this guard catches DB rows that were
    poisoned before the validator existed (or by any path that bypasses the
    service layer).
    """
