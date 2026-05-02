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
    written: bool  # False = skipped (hash already matched)
    content_hash: str  # SHA-256 of exported content


@dataclass(slots=True)
class DriftReport:
    document_id: int
    status: DriftStatus
    projection_hash: str | None  # stored in DB
    db_content_hash: str  # SHA-256 of current DB content
    file_hash: str | None  # SHA-256 of on-disk file; None if MISSING


class PathEscapeError(ValueError):
    """Raised when a document's stored path resolves outside its project root.

    This is a defense-in-depth guard. `validation.validate_doc_path` is the
    primary check on the write-path; this guard catches DB rows that were
    poisoned before the validator existed (or by any path that bypasses the
    service layer).
    """
