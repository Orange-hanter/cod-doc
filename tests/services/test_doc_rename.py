"""COD-010 / RFL-073: DocService — rename."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import (
    Document,
    DocumentStatus,
    DocumentType,
    EntityKind,
    Sensitivity,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import revision_service as rev

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _add_project(session, slug: str = "p") -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug=slug, title=slug.upper(), root_path=f"/tmp/{slug}", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    return proj.row_id


def _new_doc(
    session: Session, project_id: int, doc_key: str = "modules/M1-auth/overview"
) -> Document:
    return docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.ACTIVE,
        title="Auth Module Overview",
        author="human:dakh",
        owner="human:dakh",
        sensitivity=Sensitivity.INTERNAL,
        preamble="Intro paragraph.",
    )


def test_rename_updates_doc_key_and_writes_revision(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)

        renamed = docs.rename(
            session,
            document_id=doc.row_id,
            new_doc_key="modules/M1-auth/spec",
            author="human:dakh",
        )
        assert renamed.doc_key == "modules/M1-auth/spec"
        assert renamed.path == "modules/M1-auth/spec.md"

        history = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)
        assert len(history) == 2  # create + rename
        rename_rev = history[1]
        payload = json.loads(rename_rev.diff)
        assert payload["op"] == "rename"
        assert payload["from"]["doc_key"] == "modules/M1-auth/overview"
        assert payload["to"]["doc_key"] == "modules/M1-auth/spec"


def test_rename_unknown_document_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session, pytest.raises(docs.DocumentNotFoundError):
        docs.rename(session, document_id=99999, new_doc_key="x", author="x")


def test_rename_no_op_when_target_equals_current(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        proj_id = _add_project(session)
        doc = _new_doc(session, proj_id)

        result = docs.rename(
            session,
            document_id=doc.row_id,
            new_doc_key=doc.doc_key,
            new_path=doc.path,
            author="x",
        )
        assert result.row_id == doc.row_id

        history = rev.list_for_entity(session, EntityKind.DOCUMENT, doc.row_id)
        assert len(history) == 1  # only create
