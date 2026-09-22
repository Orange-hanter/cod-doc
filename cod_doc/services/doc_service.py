"""DocService — write-path for documents and their sections.

Public API:
- `create` — persist a new document (with empty section list); writes initial
  `entity_kind=DOCUMENT` revision.
- `get` / `get_sections` / `render_body` — read-paths. `render_body` reads from
  the `document_body` view (DATA_MODEL §4.3a).
- `add_section` / `patch_section` — section-level write-paths; each writes an
  `entity_kind=SECTION` revision with a unified diff. `patch_section` raises if
  the anchor is unknown — use `add_section` to create.
- `rename` — change `doc_key` / `path`; writes `entity_kind=DOCUMENT` revision.
  Cascade-update of incoming links is a stub here (real implementation:
  COD-013, LinkService.rename_cascade).

Conventions:
- All mutating ops require an `author` (per DATA_MODEL §3.5: 'agent:…',
  'human:…', 'mcp:…').
- `create` gates frontmatter through `validation.audit_frontmatter` and
  escalates `severity=error` issues (FM-002, FM-003) to `ValidationError`.
  FM-004/FM-005 freshness rules stay advisory.
- Caller owns the transaction (`transactional()` from `cod_doc.infra.db`).
"""

from __future__ import annotations

import difflib
import hashlib
import json
from datetime import UTC, datetime
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, NamedTuple

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    EntityKind,
    Section,
    Sensitivity,
)
from cod_doc.infra.models import DocumentModel, SectionModel
from cod_doc.infra.repositories import DocumentRepository, SectionRepository
from cod_doc.services import activity_service, search_service, validation
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class DeleteResult(NamedTuple):
    """Metadata returned by ``delete`` / ``list_delete_candidates``."""

    doc_key: str
    title: str
    section_count: int


class DocumentNotFoundError(LookupError):
    pass


class SectionNotFoundError(LookupError):
    pass


class SectionAlreadyExistsError(ValueError):
    pass


def _content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _unified_diff(old: str, new: str, *, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=label,
            tofile=label,
            lineterm="",
        )
    )


def _create_diff(body: str, *, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            [],
            body.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=label,
            lineterm="",
        )
    )


def content_hash(body: str) -> str:
    """Hash a section body the way the stored `content_hash` column is built."""
    return _content_hash(body)


def section_label(doc_key: str, anchor: str) -> str:
    """Label the SECTION revisions of this document carry in their diffs."""
    return f"section:{doc_key}#{anchor}"


def section_diff(old_body: str, new_body: str, *, doc_key: str, anchor: str) -> str:
    """Unified diff of a section-body change — byte-identical to what
    `patch_section` stores in the revision.

    Public so that the `--dry-run` previews of the MCP and CLI surfaces show
    exactly the text the revision would record, instead of each surface
    re-deriving the format and drifting from it (STO-010 / STO-011).
    """
    return _unified_diff(old_body, new_body, label=section_label(doc_key, anchor))


def section_create_diff(body: str, *, doc_key: str, anchor: str) -> str:
    """Unified diff `add_section` stores for a brand-new section."""
    return _create_diff(body, label=section_label(doc_key, anchor))


def _require_doc(session: Session, document_id: int) -> DocumentModel:
    model = session.get(DocumentModel, document_id)
    if model is None:
        raise DocumentNotFoundError(f"document #{document_id}")
    return model


def _gate_frontmatter(
    *,
    type: DocumentType,
    status: DocumentStatus,
    owner: str | None,
    frontmatter: dict[str, Any] | None,
) -> None:
    """Escalate `severity=error` advisory frontmatter issues to ValidationError.

    Write-path gate (COD-020): the caller must satisfy FM-002 (active⇒owner)
    and FM-003 (source_of_truth=false⇒canonical_source). FM-004/FM-005 are
    advisory and stay non-fatal — they're surfaced by `cod-doc audit`.
    """
    fm = frontmatter or {}
    sot = bool(fm.get("source_of_truth", True))
    issues = validation.audit_frontmatter(
        type=type,
        status=status,
        owner=owner,
        source_of_truth=sot,
        frontmatter=fm,
    )
    for issue in issues:
        if issue.severity == "error":
            raise validation.ValidationError(issue.code, issue.message, **issue.details)


def create(
    session: Session,
    *,
    project_id: int,
    doc_key: str,
    type: DocumentType,
    status: DocumentStatus,
    title: str,
    author: str,
    path: str | None = None,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    owner: str | None = None,
    source_of_truth: bool | None = None,
    preamble: str = "",
    frontmatter: dict[str, Any] | None = None,
    reason: str | None = None,
    reindex: bool = True,
) -> Document:
    """Persist a new document and write its initial revision.

    ``reindex=False`` — see :func:`add_section`.
    """
    _gate_frontmatter(
        type=type,
        status=status,
        owner=owner,
        frontmatter=frontmatter,
    )
    effective_path = path or f"{doc_key}.md"
    validation.validate_doc_path(effective_path)
    now = datetime.now(UTC)
    fm_source = (frontmatter or {}).get("source_of_truth")
    effective_source_of_truth = (
        fm_source
        if source_of_truth is None and isinstance(fm_source, bool)
        else (True if source_of_truth is None else source_of_truth)
    )
    doc = DocumentRepository(session).add(
        Document(
            project_id=project_id,
            doc_key=doc_key,
            path=effective_path,
            type=type,
            status=status,
            title=title,
            source_of_truth=effective_source_of_truth,
            sensitivity=sensitivity,
            owner=owner,
            preamble=preamble,
            frontmatter=frontmatter or {},
            created=now,
            last_updated=now,
        )
    )
    assert doc.row_id is not None
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_id=doc.row_id,
        author=author,
        diff=_create_diff(preamble, label=f"document:{doc_key}"),
        reason=reason or "create",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "doc.created",
        author,
        scope_kind="doc",
        scope_id=doc_key,
        payload={"type": type.value, "status": status.value, "path": effective_path},
        summary=f"Document {doc_key} created",
    )
    # ADO-211: без этого документ из doc_create не находился поиском.
    if reindex:
        search_service.index_doc(session, project_id=project_id, doc_key=doc_key)
    return doc


def get(session: Session, project_id: int, doc_key: str) -> Document | None:
    return DocumentRepository(session).get_by_key(project_id, doc_key)


def get_by_path(session: Session, project_id: int, path: str) -> Document | None:
    """Документ по пути его markdown-проекции. См. ADO-109."""
    return DocumentRepository(session).get_by_path(project_id, path)


def list_for_project(session: Session, project_id: int) -> list[Document]:
    return DocumentRepository(session).list_for_project(project_id)


def get_section_by_id(session: Session, section_id: int) -> Section | None:
    """Return one Section domain object by its row_id, or None."""
    sec_model = session.get(SectionModel, section_id)
    if sec_model is None:
        return None
    return SectionRepository(session).get(sec_model.row_id)


def get_doc_by_id(session: Session, document_id: int) -> Document | None:
    """Return one Document domain object by its row_id, or None."""
    return DocumentRepository(session).get(document_id)


def get_sections(session: Session, document_id: int) -> list[Section]:
    return SectionRepository(session).list_for_document(document_id)


def render_body(session: Session, document_id: int) -> str | None:
    """Full body via the `document_body` view; None if document is absent.

    Returns preamble + concatenated section bodies (with markdown headings),
    in `section.position` order — see DATA_MODEL §4.3a.
    """
    return session.execute(
        text("SELECT body FROM document_body WHERE document_id = :d"),
        {"d": document_id},
    ).scalar_one_or_none()


def add_section(
    session: Session,
    *,
    document_id: int,
    anchor: str,
    heading: str,
    level: int,
    position: int,
    body: str,
    author: str,
    reason: str | None = None,
    reindex: bool = True,
) -> Section:
    """Add a new section to a document; writes a SECTION revision.

    ``reindex=False`` — for callers that add many sections and index the
    document once at the end (``import_service``): the FTS row is built from
    every section, so per-call reindexing is quadratic in section count.

    Raises `SectionAlreadyExistsError` if a section with the same anchor
    already exists in this document.
    """
    doc = _require_doc(session, document_id)

    try:
        # Use a savepoint so that an IntegrityError on duplicate anchor rolls
        # back only the nested transaction, leaving the outer session usable.
        with session.begin_nested():
            section = SectionRepository(session).add(
                Section(
                    document_id=document_id,
                    anchor=anchor,
                    heading=heading,
                    level=level,
                    position=position,
                    body=body,
                    content_hash=_content_hash(body),
                )
            )
    except IntegrityError as exc:
        raise SectionAlreadyExistsError(
            f"section {anchor!r} already exists in document #{document_id}"
        ) from exc
    assert section.row_id is not None

    doc.last_updated = datetime.now(UTC)
    rev.write(
        session,
        project_id=doc.project_id,
        entity_kind=EntityKind.SECTION,
        entity_id=section.row_id,
        author=author,
        diff=section_create_diff(body, doc_key=doc.doc_key, anchor=anchor),
        reason=reason or "add_section",
    )
    activity_service.emit_for_write(
        session,
        doc.project_id,
        "doc.section_added",
        author,
        scope_kind="doc",
        scope_id=doc.doc_key,
        payload={"anchor": anchor, "heading": heading, "position": position},
        summary=f"Document {doc.doc_key}: section {anchor} added",
    )
    _sync_section_links_safe(session, section.row_id)
    if reindex:
        search_service.index_doc(session, project_id=doc.project_id, doc_key=doc.doc_key)
    return section


SKIP_AUTO_LINK_SYNC = "_cod_doc_skip_auto_link_sync"


def _sync_section_links_safe(session: Session, section_id: int) -> None:
    """COD-079: refresh the parsed-link rows for a section after a write.

    Best-effort: any error is logged but never bubbles up — the document
    write is the primary operation, links are derived data we can rebuild
    later via `cod-doc link backfill`.

    Callers that manage link rows themselves (e.g. ``rename_cascade``,
    which pre-updates link.raw + to_doc_key in place before patching the
    body) can opt out by setting ``session.info[SKIP_AUTO_LINK_SYNC] = True``
    for the duration of their work; the cascade re-asserts the resolved
    state on its own and a redundant auto-sync would wipe it.
    """
    if session.info.get(SKIP_AUTO_LINK_SYNC):
        return
    try:
        from cod_doc.services import link_service as _links

        # sync first (delete + re-parse from body) so a body change invalidates
        # stale rows; resolve_section then walks the fresh rows and populates
        # to_doc_key / to_task_id where the target exists. resolve_section
        # alone would not pick up body changes when rows already existed.
        _links.sync_section(session, section_id)
        _links.resolve_section(session, section_id)
    except Exception:
        import logging

        logging.getLogger("cod_doc.services.doc_service").warning(
            "sync_section failed for section_id=%s — links will be out of date "
            "until next manual sync",
            section_id,
            exc_info=True,
        )


def patch_section(
    session: Session,
    *,
    document_id: int,
    anchor: str,
    new_body: str,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | object | None = rev.NO_PARENT_CHECK,
    reindex: bool = True,
) -> Section:
    """Replace a section's body; writes a SECTION revision with unified diff.

    ``reindex=False`` — see :func:`add_section`.

    No-op if `new_body` equals the current body (no row update, no revision).
    """
    doc = _require_doc(session, document_id)

    stmt = select(SectionModel).where(
        SectionModel.document_id == document_id, SectionModel.anchor == anchor
    )
    sec_model = session.execute(stmt).scalar_one_or_none()
    if sec_model is None:
        raise SectionNotFoundError(f"section {doc.doc_key}#{anchor}")

    if sec_model.body == new_body:
        no_change = SectionRepository(session).get(sec_model.row_id)
        assert no_change is not None
        return no_change

    diff = section_diff(sec_model.body, new_body, doc_key=doc.doc_key, anchor=anchor)
    sec_model.body = new_body
    sec_model.content_hash = _content_hash(new_body)
    doc.last_updated = datetime.now(UTC)
    session.flush()

    rev.write(
        session,
        project_id=doc.project_id,
        entity_kind=EntityKind.SECTION,
        entity_id=sec_model.row_id,
        author=author,
        diff=diff,
        reason=reason,
        expected_parent_revision_id=expected_parent_revision_id,
    )
    activity_service.emit_for_write(
        session,
        doc.project_id,
        "doc.section_updated",
        author,
        scope_kind="section",
        scope_id=f"{doc.doc_key}#{anchor}",
        payload={"anchor": anchor},
        summary=f"Document {doc.doc_key}: section {anchor} updated",
    )
    _sync_section_links_safe(session, sec_model.row_id)
    if reindex:
        search_service.index_doc(session, project_id=doc.project_id, doc_key=doc.doc_key)
    refreshed = SectionRepository(session).get(sec_model.row_id)
    assert refreshed is not None
    return refreshed


def update_status(
    session: Session,
    *,
    document_id: int,
    new_status: DocumentStatus,
    author: str,
    reason: str | None = None,
) -> Document:
    """Promote/demote a document's lifecycle status — DRAFT → REVIEW → ACTIVE
    (and DEPRECATED). Writes a DOCUMENT revision capturing the transition.

    No-op when the new status equals the current one (no revision written).
    """
    doc = _require_doc(session, document_id)
    if doc.status == new_status.value:
        cached = DocumentRepository(session).get(document_id)
        assert cached is not None
        return cached

    old_status = doc.status
    doc.status = new_status.value
    doc.last_updated = datetime.now(UTC)
    session.flush()

    diff = json.dumps({"op": "status", "from": old_status, "to": new_status.value})
    rev.write(
        session,
        project_id=doc.project_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_id=document_id,
        author=author,
        diff=diff,
        reason=reason or "status",
    )
    activity_service.emit_for_write(
        session,
        doc.project_id,
        "doc.status_changed",
        author,
        scope_kind="doc",
        scope_id=doc.doc_key,
        payload={"old_status": old_status, "new_status": new_status.value, "reason": reason},
        summary=f"Document {doc.doc_key}: {old_status} → {new_status.value}",
    )
    refreshed = DocumentRepository(session).get(document_id)
    assert refreshed is not None
    return refreshed


def accept(
    session: Session,
    *,
    document_id: int,
    author: str,
    reason: str | None = None,
) -> Document:
    """Promote DRAFT/REVIEW → ACTIVE (the COD-052 'accept' transition)."""
    return update_status(
        session,
        document_id=document_id,
        new_status=DocumentStatus.ACTIVE,
        author=author,
        reason=reason or "accept",
    )


def rename(
    session: Session,
    *,
    document_id: int,
    new_doc_key: str,
    author: str,
    new_path: str | None = None,
    reason: str | None = None,
    cascade_links: bool = True,
) -> Document:
    """Change `doc_key` / `path`; writes a DOCUMENT revision.

    When `cascade_links=True` (default), runs `LinkService.rename_cascade`
    inside the same transaction to update incoming `link.to_doc_key` rows
    and rewrite canonical refs in section bodies. Pass `cascade_links=False`
    only when the caller will run the cascade later (e.g. bulk import).
    """
    doc = _require_doc(session, document_id)
    old_key = doc.doc_key
    old_path = doc.path
    target_path = new_path or f"{new_doc_key}.md"
    validation.validate_doc_path(target_path)

    if old_key == new_doc_key and old_path == target_path:
        no_change = DocumentRepository(session).get(document_id)
        assert no_change is not None
        return no_change

    doc.doc_key = new_doc_key
    doc.path = target_path
    doc.last_updated = datetime.now(UTC)
    session.flush()

    diff = json.dumps(
        {
            "op": "rename",
            "from": {"doc_key": old_key, "path": old_path},
            "to": {"doc_key": new_doc_key, "path": target_path},
        }
    )
    rev.write(
        session,
        project_id=doc.project_id,
        entity_kind=EntityKind.DOCUMENT,
        entity_id=document_id,
        author=author,
        diff=diff,
        reason=reason or "rename",
    )
    activity_service.emit_for_write(
        session,
        doc.project_id,
        "doc.renamed",
        author,
        scope_kind="doc",
        scope_id=new_doc_key,
        payload={
            "old_doc_key": old_key,
            "old_path": old_path,
            "new_doc_key": new_doc_key,
            "new_path": target_path,
        },
        summary=f"Document {old_key} renamed to {new_doc_key}",
    )

    path_changed = old_path != target_path
    if cascade_links and (old_key != new_doc_key or path_changed):
        # Local import to avoid a cycle: link_service imports doc_service.
        from cod_doc.services import link_service as _links

        _links.rename_cascade(
            session,
            project_id=doc.project_id,
            old_doc_key=old_key,
            new_doc_key=new_doc_key,
            author=author,
            reason=reason,
            path_map={old_path: target_path} if path_changed else None,
        )

    # ADO-211: индекс ключуется по doc_key — без этого поиск отдавал бы старый
    # ref, которого больше нет, а по новому ключу не находил ничего.
    if old_key != new_doc_key:
        search_service.unindex_doc(session, project_id=doc.project_id, doc_key=old_key)
    search_service.index_doc(session, project_id=doc.project_id, doc_key=new_doc_key)
    refreshed = DocumentRepository(session).get(document_id)
    assert refreshed is not None
    return refreshed


def _section_count(session: Session, document_id: int) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(SectionModel)
            .where(SectionModel.document_id == document_id)
        ).scalar_one()
    )


def delete(
    session: Session,
    *,
    project_id: int,
    doc_key: str,
    author: str,
    reason: str | None = None,
) -> DeleteResult:
    """Delete a document and its derived rows from the DB.

    Cascades (same transaction):
      - document row
      - all section rows
      - link rows belonging to those sections
      - document_tag / doc_comment rows (DB FK CASCADE)
      - FTS db_search_idx row for this document

    Does NOT touch: revision rows (append-only audit), activity events.
    Emits one ``doc.deleted`` activity event with the section count.
    """
    doc = session.execute(
        select(DocumentModel).where(
            DocumentModel.project_id == project_id,
            DocumentModel.doc_key == doc_key,
        )
    ).scalar_one_or_none()
    if doc is None:
        raise DocumentNotFoundError(f"document {doc_key!r}")

    section_count = _section_count(session, doc.row_id)

    search_service.delete_doc(session, project_id=project_id, doc_key=doc_key)

    activity_service.emit_for_write(
        session,
        project_id,
        "doc.deleted",
        author,
        scope_kind="doc",
        scope_id=doc_key,
        payload={"doc_key": doc_key, "section_count": section_count, "reason": reason},
        summary=f"Document {doc_key} deleted ({section_count} section(s))",
    )

    session.delete(doc)
    session.flush()

    return DeleteResult(
        doc_key=doc.doc_key,
        title=doc.title,
        section_count=section_count,
    )


def list_delete_candidates(
    session: Session,
    *,
    project_id: int,
    path_glob: str | None = None,
    doc_type: str | None = None,
) -> list[DeleteResult]:
    """Return documents matching optional path glob and/or type filter.

    ``path_glob`` supports ``**`` (e.g. ``'experiments/**'``) via
    ``pathlib.PurePath.match``.
    """
    docs = DocumentRepository(session).list_for_project(project_id)
    results: list[DeleteResult] = []
    for d in docs:
        if path_glob is not None and not PurePath(d.path).match(path_glob):
            continue
        if doc_type is not None and d.type.value != doc_type:
            continue
        assert d.row_id is not None
        results.append(
            DeleteResult(
                doc_key=d.doc_key,
                title=d.title,
                section_count=_section_count(session, d.row_id),
            )
        )
    return sorted(results, key=lambda r: r.doc_key)
