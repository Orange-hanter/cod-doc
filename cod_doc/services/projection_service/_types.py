"""Result/report dataclasses + drift enum + path-escape error."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path


class DriftStatus(StrEnum):
    IN_SYNC = "in_sync"  # file == projection_hash == DB content
    STALE_EXPORT = "stale_export"  # DB changed but not yet exported
    EDITED_IN_PLACE = "edited_in_place"  # file changed after last export
    MISSING = "missing"  # file not on disk


# ADO-092: the counts key for documents whose frontmatter disagrees with the
# DB. Not a DriftStatus member — those four are a partition of the content
# states, and a metadata mismatch is independent of which one a document is in.
METADATA_MISMATCH_COUNT_KEY = "metadata_mismatch"

# ADO-213: the counts key for documents carrying sections the file no longer
# has. Not a DriftStatus member either, for the same reason and with a sharper
# edge: this class is invisible to all four statuses. `doc accept` pins
# `content_sha256_head`, the file then matches the pin, and the document reads
# `in_sync` while its DB body holds headings the file dropped months ago.
ORPHAN_SECTION_COUNT_KEY = "orphan_sections"

# AFT-002: hash prefix length in `as_payload(hashes="short")` — the curator's
# budget card needs a recognisable prefix, not the 64-char SHA-256.
SHORT_HASH_LEN = 12


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
    # ADO-092: frontmatter keys where the file disagrees with the DB row.
    # Orthogonal to `status` on purpose — content and metadata drift are
    # independent, and a document can have either, both or neither. Folding
    # this into DriftStatus would have forced a choice between reporting the
    # two, which is how the status loss stayed invisible behind `in_sync`.
    metadata_mismatch: tuple[str, ...] = ()
    # ADO-213: anchors of level-2 sections the DB holds and the file does not.
    # Orthogonal to `status` for the same reason as `metadata_mismatch`, and
    # the case that proves it: an accepted document reads `in_sync` with nine
    # sections in the DB against five in the file.
    orphan_sections: tuple[str, ...] = ()

    def as_payload(self, *, hashes: Literal["full", "short"] = "full") -> dict[str, object]:
        """The report as the JSON every surface returns (without doc_key/path).

        ADO-216: this dict used to be spelled out by hand in six places — MCP
        `doc_drift` / `doc_drift_all` / `ctx_drift`, CLI `doc drift --json`
        (row and issue) and `curator_service`. ADO-213 threaded
        `orphan_sections` through all six manually; `metadata_mismatch` never
        made it into any, so a document sat in `issues` as `in_sync` with no
        stated reason. One builder means a new field reaches every surface.

        `hashes="full"` is the default because external gates compare the full
        hashes (RFC 22). `hashes="short"` cuts the three hashes to
        `SHORT_HASH_LEN` characters for the curator's budget card (AFT-002,
        RFC 27 F2); `None` stays `None`, every other field is unchanged.
        """
        if hashes not in ("full", "short"):
            raise ValueError(f"hashes must be 'full' or 'short', got {hashes!r}")

        def _hash(value: str | None) -> str | None:
            if hashes == "short" and value is not None:
                return value[:SHORT_HASH_LEN]
            return value

        return {
            "status": self.status.value,
            "projection_hash": _hash(self.projection_hash),
            "db_content_hash": _hash(self.db_content_hash),
            "file_hash": _hash(self.file_hash),
            # Both reported beside `status`, never folded into it: an accepted
            # document reads `in_sync` while its frontmatter says what the enum
            # cannot hold (ADO-092) or its DB body carries headings the file
            # dropped (ADO-213). Cures: teach the value / `doc import --replace`.
            "metadata_mismatch": list(self.metadata_mismatch),
            "orphan_sections": list(self.orphan_sections),
        }


@dataclass(slots=True)
class ProjectDriftItem:
    doc_key: str
    path: str
    report: DriftReport

    def as_payload(self, *, hashes: Literal["full", "short"] = "full") -> dict[str, object]:
        """One `issues` row: the document's identity plus its report."""
        return {
            "doc_key": self.doc_key,
            "path": self.path,
            **self.report.as_payload(hashes=hashes),
        }


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
