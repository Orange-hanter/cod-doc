"""ImportService metadata sync tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import import_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectRepository(session).add(
        ProjectEntity(slug="demo", title="Demo", root_path="/tmp/demo", config={})
    )
    proj.created = now
    proj.updated = now
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_import_or_update_syncs_existing_document_frontmatter(
    engine_with_schema,
) -> None:  # type: ignore[no-untyped-def]
    first = """---
title: Old title
type: guide
status: draft
owner: team-a
sensitivity: public
source_of_truth: false
canonical_source: docs/canonical
reviewed_on: 2026-06-05
---
# Ignored h1

Preamble v1.

## Intro

Body v1.
"""
    second = """---
title: New title
type: architecture
status: active
owner: team-b
sensitivity: confidential
source_of_truth: true
reviewed_on: 2026-06-05
---
# Ignored h1

Preamble v2.

## Intro

Body v2.
"""

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc, created = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="docs/sample",
            raw_markdown=first,
            source_sha256=_sha(first),
        )
        assert created is True

        same_doc, created = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="docs/sample",
            raw_markdown=second,
            source_sha256=_sha(second),
        )
        assert created is False
        assert same_doc.row_id == doc.row_id

        model = session.get(DocumentModel, doc.row_id)
        assert model is not None
        assert model.title == "New title"
        assert model.type == "architecture"
        assert model.status == "active"
        assert model.owner == "team-b"
        assert model.sensitivity == "confidential"
        assert model.source_of_truth is True
        assert model.preamble == "Preamble v2."
        assert model.frontmatter_json["reviewed_on"] == "2026-06-05"
        assert model.content_sha256_head == _sha(second)
        assert model.projection_hash


def test_import_stores_effective_sensitivity_in_frontmatter_json(
    engine_with_schema,
) -> None:  # type: ignore[no-untyped-def]
    raw = "# Plain\n\n## Intro\n\nBody.\n"

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc, created = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="plain",
            raw_markdown=raw,
            source_sha256=_sha(raw),
        )

        assert created is True
        model = session.get(DocumentModel, doc.row_id)
        assert model is not None
        assert model.sensitivity == "internal"
        assert model.frontmatter_json["sensitivity"] == "internal"
