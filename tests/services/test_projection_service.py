"""COD-023: ProjectionService — export / detect_drift / import."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import projection_service as proj

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def root_path(tmp_path: Path) -> Path:
    p = tmp_path / "mirror"
    p.mkdir()
    return p


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj_model = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj_model.created = now
    proj_model.updated = now
    session.add(proj_model)
    session.flush()
    return proj_model.row_id


def _make_doc(
    session: Session,
    project_id: int,
    *,
    doc_key: str = "test-doc",
    title: str = "Test Document",
    owner: str | None = "backend-team",
    status: DocumentStatus = DocumentStatus.ACTIVE,
) -> int:
    doc = docs.create(
        session,
        project_id=project_id,
        doc_key=doc_key,
        type=DocumentType.GUIDE,
        status=status,
        title=title,
        owner=owner,
        author="human:test",
    )
    return doc.row_id  # type: ignore[return-value]


# ============================================================================ #
# render_markdown                                                               #
# ============================================================================ #


def test_render_markdown_includes_frontmatter(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        md = proj.render_markdown(session, doc_id)
        assert md.startswith("---\n")
        assert "type: guide" in md
        assert "status: active" in md
        assert "owner: backend-team" in md


def test_render_markdown_includes_section_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="intro",
            heading="Introduction",
            level=2,
            position=0,
            body="Hello from section.\n",
            author="human:test",
        )

        md = proj.render_markdown(session, doc_id)
        assert "## Introduction" in md
        assert "Hello from section." in md


def test_render_markdown_unknown_doc_raises(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session, pytest.raises(docs.DocumentNotFoundError):
        proj.render_markdown(session, 9999)


# ============================================================================ #
# export_document                                                               #
# ============================================================================ #


def test_export_creates_file_and_updates_hash(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p, doc_key="modules/guide")

        result = proj.export_document(session, doc_id, root_path=root_path)

        assert result.written is True
        assert result.path == root_path / "modules/guide.md"
        assert result.path.exists()
        assert result.content_hash  # non-empty hash

        from cod_doc.infra.models import DocumentModel

        doc_model = session.get(DocumentModel, doc_id)
        assert doc_model.projection_hash == result.content_hash


def test_export_skips_when_hash_matches(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        first = proj.export_document(session, doc_id, root_path=root_path)
        assert first.written is True

        second = proj.export_document(session, doc_id, root_path=root_path)
        assert second.written is False
        assert second.content_hash == first.content_hash


def test_export_force_rewrites_even_when_in_sync(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        proj.export_document(session, doc_id, root_path=root_path)
        result = proj.export_document(session, doc_id, root_path=root_path, force=True)
        assert result.written is True


def test_export_creates_parent_directories(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p, doc_key="a/b/c/nested")

        result = proj.export_document(session, doc_id, root_path=root_path)
        assert result.path.exists()
        assert result.path.parent.is_dir()


def test_export_reruns_after_content_change(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        first = proj.export_document(session, doc_id, root_path=root_path)

        # Modify the document; hash in DB should differ from projection_hash.
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="new-section",
            heading="New",
            level=2,
            position=0,
            body="Added content.\n",
            author="human:test",
        )

        second = proj.export_document(session, doc_id, root_path=root_path)
        assert second.written is True
        assert second.content_hash != first.content_hash


# ============================================================================ #
# detect_drift                                                                  #
# ============================================================================ #


def test_detect_drift_missing_when_never_exported(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.MISSING


def test_detect_drift_in_sync_after_export(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        proj.export_document(session, doc_id, root_path=root_path)
        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.IN_SYNC


def test_detect_drift_stale_export_after_db_change(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        proj.export_document(session, doc_id, root_path=root_path)

        # Mutate DB without re-exporting.
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="s",
            heading="S",
            level=2,
            position=0,
            body="X\n",
            author="human:test",
        )

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.STALE_EXPORT


def test_detect_drift_edited_in_place(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        result = proj.export_document(session, doc_id, root_path=root_path)

        # Simulate in-place edit of the markdown file.
        result.path.write_text(result.path.read_text() + "\n# Extra\n", encoding="utf-8")

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.EDITED_IN_PLACE


def test_detect_drift_accepts_imported_file_hash_baseline(
    engine_with_schema, root_path: Path
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        imported = "---\ntitle: Imported\n---\n# Imported\n\nHand-kept markdown.\n"
        path = root_path / "test-doc.md"
        path.write_text(imported, encoding="utf-8")

        from cod_doc.infra.models import DocumentModel
        from cod_doc.services.projection_service._safety import _sha256
        from cod_doc.services.projection_service.render import render_markdown

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.content_sha256_head = _sha256(imported)
        model.projection_hash = _sha256(render_markdown(session, doc_id))
        session.flush()

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.IN_SYNC

        path.write_text(imported + "\nLocal edit.\n", encoding="utf-8")
        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.EDITED_IN_PLACE


def test_detect_project_drift_returns_counts_and_issues(
    engine_with_schema, root_path: Path
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        synced_id = _make_doc(session, p, doc_key="synced")
        _make_doc(session, p, doc_key="missing")
        proj.export_document(session, synced_id, root_path=root_path)

        report = proj.detect_project_drift(session, p, root_path=root_path)

    assert report.total_docs == 2
    assert report.counts["in_sync"] == 1
    assert report.counts["missing"] == 1
    assert report.problem_count == 1
    assert report.issues[0].doc_key == "missing"


# ============================================================================ #
# import_document                                                               #
# ============================================================================ #


def test_import_returns_none_for_untracked_file(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        unknown = root_path / "unknown.md"
        unknown.write_text("---\ntype: guide\n---\n", encoding="utf-8")
        result = proj.import_document(session, p, unknown, author="human:test", root_path=root_path)
        assert result is None


def test_import_no_op_when_hash_matches(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        export_result = proj.export_document(session, doc_id, root_path=root_path)

        report = proj.import_document(
            session,
            p,
            export_result.path,
            author="human:test",
            root_path=root_path,
        )
        assert report is not None
        assert report.document.row_id == doc_id
        assert report.warnings == []


def test_import_applies_frontmatter_field_changes(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p, status=DocumentStatus.DRAFT)

        export_result = proj.export_document(session, doc_id, root_path=root_path)

        # Edit the file to change status from draft to active.
        old_content = export_result.path.read_text(encoding="utf-8")
        new_content = old_content.replace("status: draft", "status: active")
        export_result.path.write_text(new_content, encoding="utf-8")

        report = proj.import_document(
            session,
            p,
            export_result.path,
            author="human:test",
            root_path=root_path,
        )
        assert report is not None
        assert report.document.status is DocumentStatus.ACTIVE


def test_import_applies_body_changes_and_accepts_file_baseline(
    engine_with_schema, root_path: Path
) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)
        docs.add_section(
            session,
            document_id=doc_id,
            anchor="body",
            heading="Body",
            level=2,
            position=0,
            body="Before.\n",
            author="human:test",
        )
        export_result = proj.export_document(session, doc_id, root_path=root_path)

        new_content = export_result.path.read_text(encoding="utf-8").replace(
            "Before.",
            "After.",
        )
        export_result.path.write_text(new_content, encoding="utf-8")

        imported = proj.import_document(
            session,
            p,
            export_result.path,
            author="human:test",
            root_path=root_path,
        )

        assert imported is not None
        section = next(s for s in docs.get_sections(session, doc_id) if s.anchor == "body")
        assert section.body == "After."
        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.IN_SYNC


# ============================================================================ #
# Path traversal — defense-in-depth in projection_service                       #
# ============================================================================ #


def test_export_refuses_absolute_path_in_db(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    """If a poisoned absolute path lands in document.path (bypassing validation),
    export_document must refuse to write outside the project root.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        # Poison the DB directly to simulate a row that bypassed validate_doc_path.
        from cod_doc.infra.models import DocumentModel

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.path = "/etc/cod_doc_pwned"
        session.flush()

        with pytest.raises(proj.PathEscapeError):
            proj.export_document(session, doc_id, root_path=root_path)

        assert not Path("/etc/cod_doc_pwned").exists()


def test_export_refuses_dotdot_path_in_db(
    engine_with_schema, root_path: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    """`..` segments must be rejected by the resolved-containment check."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        from cod_doc.infra.models import DocumentModel

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.path = "../escaped.md"
        session.flush()

        with pytest.raises(proj.PathEscapeError):
            proj.export_document(session, doc_id, root_path=root_path)

        # Confirm no file landed in tmp_path's parent (the escape target).
        assert not (tmp_path / "escaped.md").exists()


def test_drift_refuses_absolute_path_in_db(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    """detect_drift must also refuse to read poisoned out-of-root paths."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        p = _seed_project(session)
        doc_id = _make_doc(session, p)

        from cod_doc.infra.models import DocumentModel

        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.path = "/etc/passwd"
        session.flush()

        with pytest.raises(proj.PathEscapeError):
            proj.detect_drift(session, doc_id, root_path=root_path)


def test_create_rejects_absolute_path() -> None:
    """The primary write-path guard: validate_doc_path is invoked from doc_service.create."""
    from cod_doc.services import validation

    with pytest.raises(validation.ValidationError) as exc:
        validation.validate_doc_path("/Users/victim/.ssh/authorized_keys")
    assert exc.value.code == "SD-100"


def test_drift_reports_a_status_the_db_disagrees_with(engine_with_schema, root_path: Path) -> None:  # type: ignore[no-untyped-def]
    """ADO-092: the check that would have caught the coerced statuses.

    `detect_drift` compares content hashes, and content is rendered *from* the
    DB — so a row whose status was coerced on import renders a file byte-equal
    to the one on disk and reports `in_sync`. That is exactly how 36 coerced
    documents stayed invisible for three days behind `edited_in_place: 0`.

    Content stays untouched here: only the frontmatter disagrees, and the
    report has to say so anyway.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc_id = _make_doc(session, project_id, status=DocumentStatus.ACTIVE)
        target = proj.export_document(session, doc_id, root_path=root_path).path

        target.write_text(
            target.read_text(encoding="utf-8").replace("status: active", "status: authoritative"),
            encoding="utf-8",
        )

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.metadata_mismatch == ("status",)


def test_drift_reports_a_status_no_version_of_the_db_can_hold(  # type: ignore[no-untyped-def]
    engine_with_schema, root_path: Path
) -> None:
    """The unstorable value is the one worth reporting most.

    A representable divergence is rendered into the file, so it also moves the
    content hash and `detect_drift` already catches it. An unstorable one takes
    the `_raw_matches_db` escape hatch: the file is re-emitted verbatim, the
    hashes agree, and nothing else in the system says the DB holds something
    different. That is precisely the shape ADO-092 had, and staying silent here
    is what let it live for three days behind `edited_in_place: 0`.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc_id = _make_doc(session, project_id, status=DocumentStatus.ACTIVE)
        target = proj.export_document(session, doc_id, root_path=root_path).path

        target.write_text(
            target.read_text(encoding="utf-8").replace("status: active", "status: marinated"),
            encoding="utf-8",
        )

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.metadata_mismatch == ("status",)


@pytest.mark.parametrize("status", [DocumentStatus.RESOLVED, DocumentStatus.DONE])
def test_terminal_work_status_round_trips_without_metadata_drift(  # type: ignore[no-untyped-def]
    engine_with_schema, root_path: Path, status: DocumentStatus
) -> None:
    """ADO-218: a closed audit / closed plan is no longer metadata drift.

    Until the enum held `resolved` and `done`, every file carrying them was
    reported by `metadata_mismatch` — 45 of them in the cod-doc corpus.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        doc_id = _make_doc(session, project_id, status=status)
        target = proj.export_document(session, doc_id, root_path=root_path).path
        assert f"status: {status.value}" in target.read_text(encoding="utf-8")

        report = proj.detect_drift(session, doc_id, root_path=root_path)
        assert report.status is proj.DriftStatus.IN_SYNC
        assert report.metadata_mismatch == ()


def test_project_drift_lists_a_metadata_mismatch_even_when_content_is_in_sync(  # type: ignore[no-untyped-def]
    engine_with_schema, root_path: Path
) -> None:
    """The summary must surface it, not only the per-document report.

    Built on the one combination that really does read `in_sync` while the DB
    and the file disagree: a status the enum cannot store. The import wrote the
    fallback into the row and kept the authored block in the file, so the
    rendered content matches byte-for-byte. Before ADO-092 such a document was
    counted `in_sync` and dropped from `issues` — an operator reading the
    summary saw a clean project while 36 rows held the wrong status.
    """
    from cod_doc.services import import_service

    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        project_id = _seed_project(session)
        raw = "---\ntype: guide\nstatus: marinated\nowner: backend-team\n---\n\n# T\n\nBody.\n"
        report = import_service.import_markdown(
            session,
            project_id=project_id,
            doc_key="canon",
            raw_markdown=raw,
            path="canon.md",
            author="human:test",
        )
        doc_id = report.document.row_id
        assert doc_id is not None
        proj.export_document(session, doc_id, root_path=root_path)

        summary = proj.detect_project_drift(session, project_id, root_path=root_path)
        assert summary.counts["metadata_mismatch"] == 1
        assert [i.doc_key for i in summary.issues] == ["canon"]
        # The point of the test: content agrees, metadata does not.
        assert summary.issues[0].report.status is proj.DriftStatus.IN_SYNC
        assert summary.issues[0].report.metadata_mismatch == ("status",)
