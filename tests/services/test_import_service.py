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

    from cod_doc.services.import_service import ImportReport


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
        first_report = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="docs/sample",
            raw_markdown=first,
            source_sha256=_sha(first),
        )
        assert first_report.created is True
        doc = first_report.document

        second_report = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="docs/sample",
            raw_markdown=second,
            source_sha256=_sha(second),
        )
        assert second_report.created is False
        assert second_report.document.row_id == doc.row_id

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
        report = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="plain",
            raw_markdown=raw,
            source_sha256=_sha(raw),
        )

        assert report.created is True
        model = session.get(DocumentModel, report.document.row_id)
        assert model is not None
        assert model.sensitivity == "internal"
        assert model.frontmatter_json["sensitivity"] == "internal"
        # A default applied to an absent key is not a substitution — no warning.
        assert report.warnings == []


# ============================================================================ #
# ADO-015: the import stops bending metadata in silence                         #
# ============================================================================ #


def _import(session: Session, project_id: int, doc_key: str, raw: str) -> ImportReport:
    return import_service.import_or_update_markdown(
        session,
        project_id=project_id,
        doc_key=doc_key,
        raw_markdown=raw,
        source_sha256=_sha(raw),
    )


def test_corpus_types_are_stored_as_authored(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`capability` / `audit-report` reach the DB intact, with nothing to report.

    These two are this repository's own documents — 13 and 23 of them — and
    every one landed in the DB as `module-spec` before ADO-015.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        for doc_key, doc_type in (
            ("cap", "capability"),
            ("rep", "audit-report"),
            ("des", "design"),
            ("jour", "journal"),
            ("pl", "plan"),
            ("an", "analysis"),
            ("res", "research"),
            ("aud", "audit"),
        ):
            report = _import(
                session, project_id, doc_key, f"---\ntype: {doc_type}\n---\n\n# T\n\nBody.\n"
            )
            model = session.get(DocumentModel, report.document.row_id)
            assert model is not None
            assert model.type == doc_type
            assert report.warnings == []


def test_unstorable_type_falls_back_but_says_so(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """A type no enum member matches still falls back — loudly.

    `kickoff-brief` exists in this repo's docs and is not a cod-doc type. The
    fallback is fine; doing it without a word is the bug.
    """
    raw = "---\ntype: kickoff-brief\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        report = _import(session, project_id, "brief", raw)

        model = session.get(DocumentModel, report.document.row_id)
        assert model is not None
        assert model.type == "module-spec"
        assert [w.to_dict() for w in report.warnings] == [
            {
                "field": "type",
                "raw": "kickoff-brief",
                "applied": "module-spec",
                "reason": "unknown",
            }
        ]


def test_foreign_statuses_map_through_the_alias_table(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`final` / `living` / `done` / `resolved` are renames, not draft documents.

    `resolved` sits here and not in the `deprecated` bucket on purpose: nine
    audit reports in this very repo carry it, and a closed audit is a finished
    document, not one withdrawn from service (§2b of the frontmatter standard).
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        for i, alien in enumerate(("final", "living", "done", "resolved")):
            report = _import(
                session, project_id, f"s{i}", f"---\nstatus: {alien}\n---\n\n# T\n\nBody.\n"
            )
            model = session.get(DocumentModel, report.document.row_id)
            assert model is not None
            assert model.status == "active", alien
            assert [w.to_dict() for w in report.warnings] == [
                {"field": "status", "raw": alien, "applied": "active", "reason": "alias"}
            ]


def test_status_outside_the_alias_table_is_a_plain_unknown(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    raw = "---\nstatus: marinated\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        report = _import(session, project_id, "odd", raw)

        model = session.get(DocumentModel, report.document.row_id)
        assert model is not None
        assert model.status == "draft"
        assert [w.to_dict() for w in report.warnings] == [
            {"field": "status", "raw": "marinated", "applied": "draft", "reason": "unknown"}
        ]


def test_reimport_of_an_unstorable_type_keeps_reporting(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """The update path reports too — it was the one that made damage permanent.

    `_update_existing_document_metadata` defaults to the type already in the
    row, so a coercion applied once used to be re-applied on every re-import,
    indistinguishable from a value the author had chosen.
    """
    raw = "---\ntype: kickoff-brief\nstatus: living\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        _import(session, project_id, "brief", raw)
        second = _import(session, project_id, "brief", raw)

        assert second.created is False
        reported = {(w.field, w.raw, w.applied, w.reason) for w in second.warnings}
        assert ("type", "kickoff-brief", "module-spec", "unknown") in reported
        assert ("status", "living", "active", "alias") in reported


def test_zairgrush_shaped_frontmatter_imports_without_coercing_the_type(
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    """The pilot's own shape: `type: journal` + `status: living`, no owner.

    Exactly one warning — the status alias. Before ADO-015 this document
    became `module-spec` / `draft` with nothing said about either.
    """
    raw = """---
title: Рабочий журнал
type: journal
status: living
version: 0.18
---

# Рабочий журнал

## 2026-08-01

Запись.
"""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        report = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="journal",
            raw_markdown=raw,
            author="human:test",
            source_sha256=_sha(raw),
        )

        model = session.get(DocumentModel, report.document.row_id)
        assert model is not None
        assert model.type == "journal"
        assert model.status == "active"
        assert model.owner == "human:test"
        assert [w.to_dict() for w in report.warnings] == [
            {"field": "status", "raw": "living", "applied": "active", "reason": "alias"}
        ]


def test_full_file_sha_import_reports_in_sync_for_large_files(
    tmp_path,
    engine_with_schema,  # type: ignore[no-untyped-def]
) -> None:
    """ADO-026: web bulk apply / scan_folder hash the FULL file, so a >4 KB
    doc imported with the file's sha reports in_sync in drift and unchanged
    in the next scan (with the old 4 KB head hash both broke)."""
    big = "# Big doc\n\n" + ("lorem ipsum dolor sit amet, consectetur\n" * 400)
    assert len(big.encode("utf-8")) > 4096
    (tmp_path / "big.md").write_text(big, encoding="utf-8")

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        report = import_service.import_or_update_markdown(
            session,
            project_id=project_id,
            doc_key="big",
            raw_markdown=big,
            author="human:test",
            source_sha256=import_service._file_sha256(tmp_path / "big.md"),
        )
        row_id = report.document.row_id

        from cod_doc.services import projection_service
        from cod_doc.services.projection_service import DriftStatus

        drift = projection_service.detect_project_drift(session, project_id, root_path=tmp_path)
        assert drift.counts[DriftStatus.IN_SYNC.value] == 1
        assert drift.counts[DriftStatus.EDITED_IN_PLACE.value] == 0
        assert drift.issues == []

        entries = import_service.scan_folder(session, project_id=project_id, root=tmp_path)
        by_key = {e.doc_key: e for e in entries}
        assert by_key["big"].status == "unchanged"

        model = session.get(DocumentModel, row_id)
        assert model is not None
        assert model.content_sha256_head == _sha(big)
