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
- `render_markdown(session, document_id)` — pure: render frontmatter + body to
  a markdown string without touching the filesystem.
- `export_document(session, document_id, *, root_path, force=False)` — write the
  rendered markdown to `root_path/document.path`, update `document.projection_hash`.
  Skips if DB-hash already matches `projection_hash` (idempotent), unless `force`.
- `detect_drift(session, document_id, *, root_path)` — compare DB hash, stored
  `projection_hash`, and on-disk file hash. Returns `DriftStatus`.
- `import_document(session, project_id, file_path, *, author, root_path)` — read
  a file from disk, compare its hash with `projection_hash`; if different, parse
  the YAML frontmatter and apply field changes through `DocService`. Returns the
  updated `Document` or `None` if the document isn't tracked in the DB.

Caller owns the transaction.
"""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import yaml
from sqlalchemy import select

from cod_doc.domain.entities import Document, DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.models import DocumentModel
from cod_doc.infra.repositories import DocumentRepository
from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session


class DriftStatus(StrEnum):
    IN_SYNC = "in_sync"                # file == projection_hash == DB content
    STALE_EXPORT = "stale_export"      # DB changed but not yet exported
    EDITED_IN_PLACE = "edited_in_place"  # file changed after last export
    MISSING = "missing"                # file not on disk


@dataclass(slots=True)
class ExportResult:
    document_id: int
    path: Path
    written: bool       # False = skipped (hash already matched)
    content_hash: str   # SHA-256 of exported content


@dataclass(slots=True)
class DriftReport:
    document_id: int
    status: DriftStatus
    projection_hash: str | None    # stored in DB
    db_content_hash: str           # SHA-256 of current DB content
    file_hash: str | None          # SHA-256 of on-disk file; None if MISSING


# --------------------------------------------------------------------------- #
# Internals                                                                     #
# --------------------------------------------------------------------------- #


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_doc_model(session: Session, document_id: int) -> DocumentModel:
    m = session.get(DocumentModel, document_id)
    if m is None:
        raise docs.DocumentNotFoundError(f"document #{document_id}")
    return m


def _frontmatter_dict(model: DocumentModel) -> dict[str, Any]:
    """Produce a deterministic frontmatter dict from the DB record."""
    fm: dict[str, Any] = {
        "type": model.type,
        "status": model.status,
        "source_of_truth": bool(model.source_of_truth),
        "sensitivity": model.sensitivity,
    }
    if model.owner:
        fm["owner"] = model.owner
    if model.title:
        fm["title"] = model.title
    # Merge stored frontmatter_json for extra fields (tags, schema, …).
    extra = dict(model.frontmatter_json or {})
    # Never overwrite computed fields.
    # Never overwrite computed fields; also skip reserved fields not for users.
    for key in ("type", "status", "source_of_truth", "sensitivity", "owner", "title", "projection_hash", "doc_key", "revision"):
        extra.pop(key, None)
    fm.update(extra)
    return fm


def _render_frontmatter(fm: dict[str, Any]) -> str:
    return "---\n" + yaml.dump(fm, sort_keys=True, allow_unicode=True) + "---\n"


# --------------------------------------------------------------------------- #
# render_markdown (pure, no I/O)                                               #
# --------------------------------------------------------------------------- #


def render_markdown(session: Session, document_id: int) -> str:
    """Render the full document markdown: frontmatter + body.

    Uses `DocService.render_body` for the body (reads the `document_body` view).
    Does NOT modify the DB. Raises `DocumentNotFoundError` if unknown.
    """
    model = _require_doc_model(session, document_id)
    fm = _frontmatter_dict(model)
    frontmatter_block = _render_frontmatter(fm)
    body = docs.render_body(session, document_id) or ""
    return frontmatter_block + body


# --------------------------------------------------------------------------- #
# export_document                                                               #
# --------------------------------------------------------------------------- #


def export_document(
    session: Session,
    document_id: int,
    *,
    root_path: Path,
    force: bool = False,
) -> ExportResult:
    """Write the document projection to disk and update `projection_hash`.

    Idempotent: skips the write if `projection_hash` already matches the current
    DB content (no changes since last export), unless `force=True`.

    Returns `ExportResult` with `written=False` on a skipped export.
    """
    model = _require_doc_model(session, document_id)
    content = render_markdown(session, document_id)
    content_hash = _sha256(content)

    target = root_path / model.path
    if not force and model.projection_hash == content_hash:
        return ExportResult(
            document_id=document_id,
            path=target,
            written=False,
            content_hash=content_hash,
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    model.projection_hash = content_hash
    session.flush()

    return ExportResult(
        document_id=document_id,
        path=target,
        written=True,
        content_hash=content_hash,
    )


# --------------------------------------------------------------------------- #
# detect_drift                                                                  #
# --------------------------------------------------------------------------- #


def detect_drift(
    session: Session,
    document_id: int,
    *,
    root_path: Path,
) -> DriftReport:
    """Classify the drift state between DB, projection_hash, and disk file.

    Returns a `DriftReport` with `status` ∈ `DriftStatus`.
    """
    model = _require_doc_model(session, document_id)
    content = render_markdown(session, document_id)
    db_hash = _sha256(content)

    file_path = root_path / model.path
    if not file_path.exists():
        return DriftReport(
            document_id=document_id,
            status=DriftStatus.MISSING,
            projection_hash=model.projection_hash,
            db_content_hash=db_hash,
            file_hash=None,
        )

    file_hash = _sha256(file_path.read_text(encoding="utf-8"))

    if model.projection_hash != db_hash:
        status = DriftStatus.STALE_EXPORT
    elif file_hash != model.projection_hash:
        status = DriftStatus.EDITED_IN_PLACE
    else:
        status = DriftStatus.IN_SYNC

    return DriftReport(
        document_id=document_id,
        status=status,
        projection_hash=model.projection_hash,
        db_content_hash=db_hash,
        file_hash=file_hash,
    )


# --------------------------------------------------------------------------- #
# import_document                                                               #
# --------------------------------------------------------------------------- #


def import_document(
    session: Session,
    project_id: int,
    file_path: Path,
    *,
    author: str,
    root_path: Path,
) -> Document | None:
    """Read a projection file and apply frontmatter changes to the DB record.

    If the file's hash matches `projection_hash`, there is nothing to import
    (returns the existing Document unchanged).

    If no Document with this path exists in the project, returns `None`.

    Full section-body import (parsing markdown sections and updating the DB) is
    handled by the Restate importer (COD-051) which builds on the frontmatter
    parser (COD-050). This function handles the *structural* frontmatter fields
    only (type, status, owner, sensitivity, source_of_truth).
    """
    rel_path = str(file_path.relative_to(root_path))
    stmt = select(DocumentModel).where(
        DocumentModel.project_id == project_id,
        DocumentModel.path == rel_path,
    )
    model = session.execute(stmt).scalar_one_or_none()
    if model is None:
        return None

    content = file_path.read_text(encoding="utf-8")
    file_hash = _sha256(content)
    if file_hash == model.projection_hash:
        return DocumentRepository(session)._to_domain(model)

    # Parse frontmatter from the file.
    fm = _parse_frontmatter(content)
    _apply_frontmatter_to_model(model, fm)
    session.flush()
    return DocumentRepository(session)._to_domain(model)


def _parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    yaml_block = content[3:end].strip()
    try:
        return yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        return {}


def _apply_frontmatter_to_model(model: DocumentModel, fm: dict[str, Any]) -> None:
    """Apply recognised frontmatter fields to the ORM model (in-place)."""
    if "type" in fm:
        with contextlib.suppress(ValueError):
            model.type = DocumentType(fm["type"]).value
    if "status" in fm:
        with contextlib.suppress(ValueError):
            model.status = DocumentStatus(fm["status"]).value
    if "owner" in fm:
        model.owner = str(fm["owner"]) if fm["owner"] else None
    if "sensitivity" in fm:
        with contextlib.suppress(ValueError):
            model.sensitivity = Sensitivity(fm["sensitivity"]).value
    if "source_of_truth" in fm and isinstance(fm["source_of_truth"], bool):
        model.source_of_truth = fm["source_of_truth"]


__all__ = [
    "DriftReport",
    "DriftStatus",
    "ExportResult",
    "detect_drift",
    "export_document",
    "import_document",
    "render_markdown",
]
