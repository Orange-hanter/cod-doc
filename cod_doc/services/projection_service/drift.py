"""detect_drift — classify DB ↔ projection_hash ↔ on-disk file state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frontmatter import metadata_mismatches
from ._internals import _require_doc_model
from ._safety import _safe_target, _sha256
from ._types import (
    METADATA_MISMATCH_COUNT_KEY,
    ORPHAN_SECTION_COUNT_KEY,
    DriftReport,
    DriftStatus,
    ProjectDriftItem,
    ProjectDriftReport,
)
from .render import render_markdown

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.orm import Session


def normalize_repo_path(path: str) -> str:
    """Normalise a repo-relative path for comparison (``./a/b`` → ``a/b``).

    Git and the DB agree on POSIX separators and repo-relative paths, but
    ``gh``/human input may carry a leading ``./`` or ``/``, or a Windows
    separator. Anything else is left untouched — this is a comparison key,
    not a filesystem operation.
    """
    cleaned = path.strip().replace("\\", "/").lstrip("/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def orphan_section_anchors(session: Session, document_id: int, file_text: str) -> tuple[str, ...]:
    """Anchors of level-2 DB sections this file's headings do not account for.

    ADO-213. Reported beside `DriftStatus`, never folded into it: the four
    statuses are a partition of *content hash* states, and this class slips
    through all of them. `doc accept` pins `content_sha256_head`, the file
    keeps matching that pin, and the document reads `in_sync` while the DB
    body carries headings the file dropped.

    Scoped to level 2 because `## ` is the only heading `parse_markdown`
    turns into a section: a section at any other level was authored in the DB
    and the file was never able to carry it, so its absence proves nothing.

    ADO-213. Identity is `import_service.match_file_sections` — anchor first,
    then heading text — and not the anchor alone. Anchor-only comparison
    called every hand-authored anchor an orphan: on the live cod-doc DB it
    reported 29 documents / 146 sections, of which 16 / 80 were the
    `docs/system/scenarios/*` corpus, whose rows `scenario_service.export`
    writes as `scn-001` while the file heading re-derives to
    `scn-001--completing-a-task-…`. Every one of those sections is in the
    file, under its own heading.
    """
    from cod_doc.services import doc_service, import_service

    parsed = import_service.parse_markdown(file_text)
    match = import_service.match_file_sections(
        doc_service.get_sections(session, document_id), parsed.sections
    )
    return match.orphans


def detect_drift(
    session: Session,
    document_id: int,
    *,
    root_path: Path,
) -> DriftReport:
    """Classify the drift state between DB, projection_hash, and disk file.

    Returns a `DriftReport` with `status` ∈ `DriftStatus`, plus two signals
    reported independently of it: `metadata_mismatch` (ADO-092) and
    `orphan_sections` (ADO-213).
    """
    model = _require_doc_model(session, document_id)
    file_path = _safe_target(root_path, model.path)
    content = render_markdown(session, document_id)
    db_hash = _sha256(content)

    if not file_path.exists():
        return DriftReport(
            document_id=document_id,
            status=DriftStatus.MISSING,
            projection_hash=model.projection_hash,
            db_content_hash=db_hash,
            file_hash=None,
        )

    file_text = file_path.read_text(encoding="utf-8")
    file_hash = _sha256(file_text)
    mismatch = metadata_mismatches(model, file_text)
    orphans = orphan_section_anchors(session, document_id, file_text)

    accepted_file_hash = getattr(model, "content_sha256_head", None)
    if model.projection_hash != db_hash:
        status = DriftStatus.STALE_EXPORT
    elif accepted_file_hash and file_hash == accepted_file_hash:
        status = DriftStatus.IN_SYNC
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
        metadata_mismatch=mismatch,
        orphan_sections=orphans,
    )


def detect_project_drift(
    session: Session,
    project_id: int,
    *,
    root_path: Path,
    limit: int | None = None,
    paths: Sequence[str] | None = None,
) -> ProjectDriftReport:
    """Project-wide DB ↔ markdown projection drift summary.

    The report is read-only and intentionally mirrors the shape needed by CLI,
    routines, MCP, and Web health badges.

    ``paths`` (SYM-010) narrows the scan to documents whose repo-relative
    ``path`` is in the given set — the PR drift-gate feeds it the files a pull
    request touched. ``None`` scans the whole project; an empty sequence is a
    deliberate "nothing to scan" and yields an empty report.
    """
    from cod_doc.services import doc_service

    docs = doc_service.list_for_project(session, project_id)
    if paths is not None:
        wanted = {normalize_repo_path(p) for p in paths}
        docs = [d for d in docs if normalize_repo_path(d.path) in wanted]
    if limit is not None:
        docs = docs[:limit]

    counts = {status.value: 0 for status in DriftStatus}
    # ADO-092: counted separately from the four content states, because a
    # metadata mismatch can sit on a document in any of them.
    counts[METADATA_MISMATCH_COUNT_KEY] = 0
    # ADO-213: same reasoning, sharper case — an orphaned section survives
    # `doc accept` and reads `in_sync` forever.
    counts[ORPHAN_SECTION_COUNT_KEY] = 0
    issues: list[ProjectDriftItem] = []
    checked = 0

    for doc in docs:
        if doc.row_id is None:
            continue
        checked += 1
        report = detect_drift(session, doc.row_id, root_path=root_path)
        counts[report.status.value] += 1
        if report.metadata_mismatch:
            counts[METADATA_MISMATCH_COUNT_KEY] += 1
        if report.orphan_sections:
            counts[ORPHAN_SECTION_COUNT_KEY] += 1
        # A metadata mismatch is an issue even when the content is in sync —
        # that combination is exactly the one that hid the ADO-092 status loss.
        # An orphaned section is the same shape of blind spot (ADO-213).
        if (
            report.status is not DriftStatus.IN_SYNC
            or report.metadata_mismatch
            or report.orphan_sections
        ):
            issues.append(
                ProjectDriftItem(
                    doc_key=doc.doc_key,
                    path=doc.path,
                    report=report,
                )
            )

    return ProjectDriftReport(
        project_id=project_id,
        total_docs=checked,
        counts=counts,
        issues=issues,
    )
