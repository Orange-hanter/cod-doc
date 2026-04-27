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
  'human:…', 'mcp:…'). Format validation is COD-020's job.
- Caller owns the transaction (`transactional()` from `cod_doc.infra.db`).
"""

from __future__ import annotations

import difflib
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
from cod_doc.services import revision_service as rev


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


def _require_doc(session: Session, document_id: int) -> DocumentModel:
    model = session.get(DocumentModel, document_id)
    if model is None:
        raise DocumentNotFoundError(f"document #{document_id}")
    return model


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
    preamble: str = "",
    frontmatter: dict[str, Any] | None = None,
    reason: str | None = None,
) -> Document:
    """Persist a new document and write its initial revision."""
    now = datetime.now(timezone.utc)
    doc = DocumentRepository(session).add(
        Document(
            project_id=project_id,
            doc_key=doc_key,
            path=path or f"{doc_key}.md",
            type=type,
            status=status,
            title=title,
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
    return doc


def get(session: Session, project_id: int, doc_key: str) -> Document | None:
    return DocumentRepository(session).get_by_key(project_id, doc_key)


def list_for_project(session: Session, project_id: int) -> list[Document]:
    return DocumentRepository(session).list_for_project(project_id)


def get_sections(session: Session, document_id: int) -> list[Section]:
    return SectionRepository(session).list_for_document(document_id)


def render_body(session: Session, document_id: int) -> str | None:
    """Full body via the `document_body` view; None if document is absent.

    Returns preamble + concatenated section bodies (with markdown headings),
    in `section.position` order — see DATA_MODEL §4.3a.
    """
    row = session.execute(
        text("SELECT body FROM document_body WHERE document_id = :d"),
        {"d": document_id},
    ).scalar_one_or_none()
    return row


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
) -> Section:
    """Add a new section to a document; writes a SECTION revision.

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

    doc.last_updated = datetime.now(timezone.utc)
    rev.write(
        session,
        project_id=doc.project_id,
        entity_kind=EntityKind.SECTION,
        entity_id=section.row_id,
        author=author,
        diff=_create_diff(body, label=f"section:{doc.doc_key}#{anchor}"),
        reason=reason or "add_section",
    )
    return section


def patch_section(
    session: Session,
    *,
    document_id: int,
    anchor: str,
    new_body: str,
    author: str,
    reason: str | None = None,
    expected_parent_revision_id: str | None | object = rev.NO_PARENT_CHECK,
) -> Section:
    """Replace a section's body; writes a SECTION revision with unified diff.

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

    diff = _unified_diff(
        sec_model.body, new_body, label=f"section:{doc.doc_key}#{anchor}"
    )
    sec_model.body = new_body
    sec_model.content_hash = _content_hash(new_body)
    doc.last_updated = datetime.now(timezone.utc)
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
    refreshed = SectionRepository(session).get(sec_model.row_id)
    assert refreshed is not None
    return refreshed


def rename(
    session: Session,
    *,
    document_id: int,
    new_doc_key: str,
    author: str,
    new_path: str | None = None,
    reason: str | None = None,
) -> Document:
    """Change `doc_key` / `path`; writes a DOCUMENT revision.

    Cascade-update of incoming links (where `link.to_doc_key == old_doc_key`)
    is intentionally NOT done here — that is LinkService.rename_cascade in
    COD-013. Callers that need link integrity must run the cascade afterwards.
    """
    doc = _require_doc(session, document_id)
    old_key = doc.doc_key
    old_path = doc.path
    target_path = new_path or f"{new_doc_key}.md"

    if old_key == new_doc_key and old_path == target_path:
        no_change = DocumentRepository(session).get(document_id)
        assert no_change is not None
        return no_change

    doc.doc_key = new_doc_key
    doc.path = target_path
    doc.last_updated = datetime.now(timezone.utc)
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
    refreshed = DocumentRepository(session).get(document_id)
    assert refreshed is not None
    return refreshed
