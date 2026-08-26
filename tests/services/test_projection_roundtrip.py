"""ADO-010 (audit finding F7): import → export must not damage the document.

The audit found `doc export` gluing the preamble onto the first heading,
dropping the H1, and rewriting `type`. These tests pin the repaired contract
against **real repository documents** — the corpus the pilots will meet — plus
the guards that stop an export from overwriting content cod-doc did not write.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import doc_service, import_service
from cod_doc.services import projection_service as proj

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]

# One document per shape that used to break the round-trip:
#   audit-report / capability — types the DB enum used to reject, rewriting the
#     file to `module-spec`. ADO-015 added them to `DocumentType`, so these two
#     now round-trip because the DB stores what the file says, not because the
#     renderer is stepping around an unstorable value;
#   execution-plan — carries `title:` in frontmatter *and* an H1;
#   guide / standard / architecture / vision — flow-style lists, unquoted dates,
#     long preambles ahead of the first `##`;
#   HANDBOOK — no frontmatter at all: nothing may be invented for it.
REPO_DOCS = [
    "docs/system/audit/2026-07-29-state-of-the-project.md",
    "docs/system/capabilities/project-bootstrap.md",
    "docs/system/roadmap/cod-doc-task-plan.md",
    "docs/adoption-playbook.md",
    "docs/system/standards/frontmatter.md",
    "docs/system/ARCHITECTURE.md",
    "docs/system/VISION.md",
    "docs/HANDBOOK.md",
]


@pytest.fixture
def root_path(tmp_path: Path) -> Path:
    p = tmp_path / "mirror"
    p.mkdir()
    return p


def _seed_project(session: Session, root: Path) -> int:
    now = datetime.now(UTC)
    model = ProjectModel(slug="p", title="P", root_path=str(root), config_json={})
    model.created = now
    model.updated = now
    session.add(model)
    session.flush()
    return model.row_id


def _import_file(session: Session, project_id: int, rel_path: str, raw: str) -> int:
    """Import markdown as the document living at `rel_path`."""
    doc = import_service.import_markdown(
        session,
        project_id=project_id,
        doc_key=rel_path.removesuffix(".md"),
        raw_markdown=raw,
        author="human:test",
    ).document
    assert doc.row_id is not None
    model = session.get(DocumentModel, doc.row_id)
    assert model is not None
    model.path = rel_path
    session.flush()
    return doc.row_id


# ============================================================================ #
# Byte-identical round-trip on real repository documents                        #
# ============================================================================ #


@pytest.mark.parametrize("rel_path", REPO_DOCS)
def test_repo_doc_roundtrip_is_byte_identical(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
    rel_path: str,
) -> None:
    """import → export reproduces the source file byte for byte."""
    source = REPO_ROOT / rel_path
    raw = source.read_text(encoding="utf-8")
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, rel_path, raw)
        proj.export_document(session, doc_id, root_path=root_path, force_write=True)

    written = (root_path / rel_path).read_text(encoding="utf-8")
    assert written == raw


def test_export_is_a_fixed_point(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """Re-importing an exported file and exporting again changes nothing.

    Fidelity on the first pass is not enough: the projection has to be stable
    under repetition, or `doc drift` reports churn forever.
    """
    rel_path = "docs/adoption-playbook.md"
    raw = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, rel_path, raw)
        proj.export_document(session, doc_id, root_path=root_path, force_write=True)
        first = (root_path / rel_path).read_text(encoding="utf-8")

        proj.import_document(
            session,
            project_id,
            root_path / rel_path,
            author="human:test",
            root_path=root_path,
        )
        proj.export_document(session, doc_id, root_path=root_path, force=True, force_write=True)

    assert (root_path / rel_path).read_text(encoding="utf-8") == first


# ============================================================================ #
# The three F7 defects, pinned individually                                     #
# ============================================================================ #


def test_preamble_is_separated_from_first_heading(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """F7 defect 1: `…заранее.## 1. Зачем` — blockquote glued to an H2."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\n> Preamble line.\n\n## First\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert "> Preamble line.\n\n## First" in rendered
    assert "Preamble line.## First" not in rendered


def test_h1_survives_the_round_trip(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """F7 defect 2: the H1 was parsed into `title` and never rendered back."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# Kept Heading\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert rendered == raw


def test_unknown_type_is_not_rewritten(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """F7 defect 3: an unstorable `type:` came back as `module-spec`.

    `kickoff-brief` is a real type from this repo's corpus that no version of
    cod-doc can store. The DB coerces it — and ADO-015 makes it say so — but
    the file must keep the word its author wrote.
    """
    raw = "---\ntype: kickoff-brief\nstatus: active\n---\n\n# C\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert "type: kickoff-brief" in rendered
    assert "module-spec" not in rendered


def test_capability_is_stored_as_authored_and_round_trips(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """ADO-015: `capability` survives in the DB, not only in the file.

    The sibling of the test above. Before ADO-015 the file was saved by the
    renderer's unknown-value escape hatch while the row said `module-spec`;
    now the row says `capability` and the round-trip is a consequence of that,
    which is what makes filtering and export stable.
    """
    raw = "---\ntype: capability\nstatus: active\n---\n\n# C\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        model = session.get(DocumentModel, doc_id)
        assert model is not None
        assert model.type == DocumentType.CAPABILITY.value
        assert proj.render_markdown(session, doc_id) == raw


def test_nothing_is_invented_for_a_bare_markdown_file(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """A file with neither frontmatter nor H1 gets neither back."""
    raw = "## Section\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "bare.md", raw)
        rendered = proj.render_markdown(session, doc_id)

    assert rendered == raw


def test_frontmatter_is_reserialised_when_the_db_diverges(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """A DB-side status change reaches the file, keeping the source key order."""
    raw = "---\ntype: guide\nstatus: draft\nowner: docs\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.status = "active"
        session.flush()
        rendered = proj.render_markdown(session, doc_id)

    assert "status: active" in rendered
    assert rendered.index("type:") < rendered.index("status:") < rendered.index("owner:")


# ============================================================================ #
# Export guards (stage 1)                                                       #
# ============================================================================ #


def test_dry_run_reports_the_diff_and_writes_nothing(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)
    target = root_path / "d.md"
    target.write_text("hand-written\n", encoding="utf-8")

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        result = proj.export_document(session, doc_id, root_path=root_path, dry_run=True)

    assert result.written is False
    assert result.diff is not None
    assert "-hand-written" in result.diff
    assert "+# T" in result.diff
    assert target.read_text(encoding="utf-8") == "hand-written\n"


def test_export_refuses_to_overwrite_an_unknown_file(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """The file on disk matches neither the last export nor the last import."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)
    target = root_path / "d.md"
    target.write_text("someone's unsaved work\n", encoding="utf-8")

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        with pytest.raises(proj.ExportGuardError, match="edited in place"):
            proj.export_document(session, doc_id, root_path=root_path)

        proj.export_document(session, doc_id, root_path=root_path, force_write=True)

    assert target.read_text(encoding="utf-8") == raw


def test_export_refuses_a_foreign_checkout_when_asked_to(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """`own_checkout_only=True` (what CLI and MCP pass) blocks foreign roots."""
    raw = "---\ntype: guide\nstatus: active\n---\n\n# T\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc_id = _import_file(session, project_id, "d.md", raw)
        with pytest.raises(proj.ExportGuardError, match="own"):
            proj.export_document(session, doc_id, root_path=root_path, own_checkout_only=True)

        result = proj.export_document(
            session,
            doc_id,
            root_path=root_path,
            own_checkout_only=True,
            force_write=True,
        )

    assert result.written is True


# ============================================================================ #
# ADO-022: rows older than migration 0025 must not be exported blind            #
# ============================================================================ #


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _legacy_row(session: Session, doc_id: int, raw: str) -> None:
    """Rewind a document row to its pre-`0025_projection_fidelity` state.

    That migration added `frontmatter_raw` / `title_in_body`; every row written
    before it has both NULL while `content_sha256_head` still holds the sha of
    the accepted file. That is exactly the combination that satisfied the
    ADO-010 provenance guard and let a mass re-export rewrite 107 of 121 files:
    the file is provably ours, but the DB has forgotten its shape.
    `projection_hash` is stale — it was written by the pre-ADO-010 renderer, so
    it matches neither the file nor today's projection.
    """
    model = session.get(DocumentModel, doc_id)
    assert model is not None
    model.frontmatter_raw = None
    model.title_in_body = None
    model.content_sha256_head = _sha256(raw)
    model.projection_hash = _sha256(f"pre-ADO-010 render of {raw}")
    session.flush()


def _seed_legacy_doc(session: Session, root_path: Path, rel_path: str) -> tuple[int, int, str]:
    """Import a real repo document, put it on disk, then age the row."""
    raw = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    project_id = _seed_project(session, root_path)
    doc_id = _import_file(session, project_id, rel_path, raw)
    target = root_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw, encoding="utf-8")
    _legacy_row(session, doc_id, raw)
    return project_id, doc_id, raw


def test_export_refuses_when_projection_fidelity_is_unknown(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """The acceptance criterion: a legacy row does not lose its frontmatter."""
    rel_path = "docs/system/standards/frontmatter.md"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id, raw = _seed_legacy_doc(session, root_path, rel_path)
        with pytest.raises(proj.ExportGuardError, match="backfill"):
            proj.export_document(session, doc_id, root_path=root_path)

    assert (root_path / rel_path).read_text(encoding="utf-8") == raw


def test_legacy_row_without_frontmatter_is_not_given_one(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """A file that never had frontmatter must not acquire an invented block.

    The corpus damage was not limited to reordered keys: files with no
    frontmatter at all were handed `type: module-spec / status: draft /
    owner: …` out of thin air.
    """
    rel_path = "docs/HANDBOOK.md"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id, raw = _seed_legacy_doc(session, root_path, rel_path)
        rendered = proj.render_markdown(session, doc_id)
        assert rendered.startswith("---"), "precondition: the projection invents frontmatter"
        with pytest.raises(proj.ExportGuardError, match="backfill"):
            proj.export_document(session, doc_id, root_path=root_path)

    assert (root_path / rel_path).read_text(encoding="utf-8") == raw


def test_export_after_backfill_is_byte_identical(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """The positive half: backfill teaches the row its shape, export is a no-op."""
    rel_path = "docs/system/standards/frontmatter.md"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id, doc_id, raw = _seed_legacy_doc(session, root_path, rel_path)
        report = proj.backfill_projection_fidelity(session, project_id, root_path=root_path)
        assert report.filled == 1
        result = proj.export_document(session, doc_id, root_path=root_path)
        assert result.written is True

    assert (root_path / rel_path).read_text(encoding="utf-8") == raw


def test_fidelity_guard_does_not_fire_for_a_db_authored_document(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """NULL fidelity columns are also the honest state of `doc create`.

    A guard written literally to the acceptance criterion ("frontmatter_raw IS
    NULL and the file exists") would block every re-export of a document
    cod-doc authored itself.
    """
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        project_id = _seed_project(session, root_path)
        doc = doc_service.create(
            session,
            project_id=project_id,
            doc_key="authored",
            type=DocumentType.GUIDE,
            status=DocumentStatus.DRAFT,
            title="Authored",
            owner="docs",
            author="human:test",
        )
        assert doc.row_id is not None
        first = proj.export_document(session, doc.row_id, root_path=root_path)
        assert first.written is True

        model = session.get(DocumentModel, doc.row_id)
        assert model is not None
        assert model.frontmatter_raw is None and model.title_in_body is None
        model.status = "active"
        session.flush()

        second = proj.export_document(session, doc.row_id, root_path=root_path)

    assert second.written is True
    assert "status: active" in (root_path / "authored.md").read_text(encoding="utf-8")


def test_force_write_lifts_the_fidelity_guard(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    rel_path = "docs/system/standards/frontmatter.md"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id, raw = _seed_legacy_doc(session, root_path, rel_path)
        result = proj.export_document(session, doc_id, root_path=root_path, force_write=True)

    assert result.written is True
    assert (root_path / rel_path).read_text(encoding="utf-8") != raw


def test_dry_run_previews_when_fidelity_is_unknown(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    rel_path = "docs/system/standards/frontmatter.md"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id, raw = _seed_legacy_doc(session, root_path, rel_path)
        result = proj.export_document(session, doc_id, root_path=root_path, dry_run=True)

    assert result.written is False
    assert result.diff
    assert (root_path / rel_path).read_text(encoding="utf-8") == raw


# ============================================================================ #
# ADO-015: rows older than migration 0026 must not be exported blind            #
# ============================================================================ #

_CAPABILITY_DOC = "---\ntype: capability\nstatus: active\nowner: docs\n---\n\n# Cap\n\nBody.\n"


def _seed_pre_0026_doc(
    session: Session,
    root_path: Path,
    rel_path: str,
    raw: str,
    *,
    coerced_to: str = "module-spec",
) -> tuple[int, int]:
    """Import *raw*, put it on disk, then rewind the row to a pre-ADO-015 build.

    Such a build had no `capability` member, so it wrote `module-spec` into
    `document.type` while `frontmatter_json` kept the authored word. Everything
    else is a healthy row: `frontmatter_raw` / `title_in_body` are set (0025 ran)
    and `content_sha256_head` matches the file, so both older guards pass.
    """
    project_id = _seed_project(session, root_path)
    doc_id = _import_file(session, project_id, rel_path, raw)
    target = root_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw, encoding="utf-8")
    model = session.get(DocumentModel, doc_id)
    assert model is not None
    model.type = coerced_to
    model.content_sha256_head = _sha256(raw)
    session.flush()
    return project_id, doc_id


def test_export_refuses_a_row_migration_0026_has_not_repaired(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """Upgrading the package without the database must not cost the author's type.

    ADO-015 made `capability` storable, which is precisely what disarms the
    ADO-010 escape hatch that used to keep an unstorable `type:` verbatim. Until
    `0026_document_type_recoercion` has run, the row still says `module-spec` —
    and the export would copy that lie onto disk.
    """
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id = _seed_pre_0026_doc(session, root_path, "d.md", _CAPABILITY_DOC)
        with pytest.raises(proj.ExportGuardError, match="0026_document_type_recoercion"):
            proj.export_document(session, doc_id, root_path=root_path)

    assert (root_path / "d.md").read_text(encoding="utf-8") == _CAPABILITY_DOC


def test_export_is_byte_identical_once_the_type_is_recoerced(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """The positive half: with the migration applied, the export is a no-op."""
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id = _seed_pre_0026_doc(session, root_path, "d.md", _CAPABILITY_DOC)
        model = session.get(DocumentModel, doc_id)
        assert model is not None
        model.type = DocumentType.CAPABILITY.value  # what migration 0026 writes
        session.flush()
        result = proj.export_document(session, doc_id, root_path=root_path)

    assert result.written is True
    assert (root_path / "d.md").read_text(encoding="utf-8") == _CAPABILITY_DOC


def test_type_recoercion_guard_ignores_a_type_no_build_can_store(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    """`kickoff-brief` is not a pending migration — it is a permanent unknown.

    `_raw_matches_db` keeps such a block verbatim on its own, so the export must
    go through rather than demand a migration that would not change anything.
    """
    raw = "---\ntype: kickoff-brief\nstatus: active\nowner: docs\n---\n\n# K\n\nBody.\n"
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id = _seed_pre_0026_doc(session, root_path, "k.md", raw)
        result = proj.export_document(session, doc_id, root_path=root_path)

    assert result.written is True
    assert (root_path / "k.md").read_text(encoding="utf-8") == raw


def test_force_write_lifts_the_type_recoercion_guard(
    engine_with_schema,  # type: ignore[no-untyped-def]
    root_path: Path,
) -> None:
    factory = make_session_factory(engine_with_schema)

    with transactional(factory) as session:
        _project_id, doc_id = _seed_pre_0026_doc(session, root_path, "d.md", _CAPABILITY_DOC)
        result = proj.export_document(session, doc_id, root_path=root_path, force_write=True)

    assert result.written is True
    assert "type: module-spec" in (root_path / "d.md").read_text(encoding="utf-8")
