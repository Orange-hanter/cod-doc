"""COD-025 / SD-001..SD-003: SensitivityScanner + audit_sensitivity + clearance + redaction."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType, Sensitivity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import doc_service as docs
from cod_doc.services import projection_service as proj
from cod_doc.services import sensitivity_scanner as sens
from cod_doc.services import validation as v

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]


# ============================================================================ #
# SensitivityScanner — pure detection                                           #
# ============================================================================ #


def test_scanner_detects_aws_access_key() -> None:
    findings = sens.scan("Use AKIAIOSFODNN7EXAMPLE in your config.")
    kinds = [f.kind for f in findings]
    assert "aws_access_key" in kinds
    aws = next(f for f in findings if f.kind == "aws_access_key")
    assert "EXAMPLE" not in aws.snippet  # snippet is redacted
    assert aws.line == 1
    assert aws.confidence >= 0.9


def test_scanner_detects_github_pat() -> None:
    token = "ghp_" + "a" * 36
    findings = sens.scan(f"My token: {token}")
    assert any(f.kind == "github_pat" for f in findings)


def test_scanner_detects_jwt_token() -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    findings = sens.scan(f"Authorization: Bearer {jwt}")
    assert any(f.kind == "jwt_token" for f in findings)


def test_scanner_detects_pem_private_key() -> None:
    findings = sens.scan(
        "secret:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQE...\n-----END RSA PRIVATE KEY-----"
    )
    assert any(f.kind == "pem_private_key" for f in findings)


def test_scanner_detects_slack_token() -> None:
    findings = sens.scan("slack_bot=xoxb-12345678-abcdef-XXXX")
    assert any(f.kind == "slack_token" for f in findings)


def test_scanner_detects_pii_email_phone_window() -> None:
    findings = sens.scan("Contact John at john.doe@example.com or +1 555-123-4567 directly.")
    assert any(f.kind == "pii_contact" for f in findings)


def test_scanner_clean_doc_returns_no_findings() -> None:
    findings = sens.scan(
        "# Auth Module\n\nThis describes authentication. Implementation uses JWT (no real token shown).\n"
    )
    # Plain prose without long high-entropy strings should be clean.
    assert findings == []


def test_scanner_high_entropy_threshold_skips_natural_text() -> None:
    """Long English sentences must NOT trip the generic-token detector."""
    text = "The architecture team reviews documentation quarterly to keep specs current. " * 5
    findings = sens.scan(text)
    assert all(f.kind != "generic_high_entropy" for f in findings)


def test_scanner_redacts_snippet() -> None:
    findings = sens.scan("AKIAIOSFODNN7EXAMPLE")
    assert findings
    snippet = findings[0].snippet
    assert "EXAMPLE" not in snippet  # full secret never returned
    assert "…" in snippet or snippet == "[redacted]"


def test_scanner_line_numbers_are_one_based() -> None:
    content = "line 1\nline 2\nAKIAIOSFODNN7EXAMPLE\nline 4"
    findings = sens.scan(content)
    assert findings[0].line == 3


# ============================================================================ #
# clearance_meets — SD-003 prep helper                                          #
# ============================================================================ #


@pytest.mark.parametrize(
    "actor,doc,expected",
    [
        ("public", "public", True),
        ("public", "internal", False),
        ("internal", "internal", True),
        ("internal", "public", True),
        ("internal", "confidential", False),
        ("confidential", "confidential", True),
        ("confidential", "restricted", False),
        ("restricted", "restricted", True),
        ("restricted", "public", True),
        (None, "public", True),
        (None, "internal", False),
    ],
)
def test_clearance_meets(actor: str | None, doc: str, expected: bool) -> None:
    assert sens.clearance_meets(actor, doc) is expected


def test_clearance_unknown_value_treated_as_public() -> None:
    """Unknown clearance string falls back to most restrictive (public)."""
    assert sens.clearance_meets("ml-engineer", "internal") is False


# ============================================================================ #
# audit_sensitivity — advisory wrapper around scanner                            #
# ============================================================================ #


def test_audit_sensitivity_clean_body_returns_no_issues() -> None:
    issues = v.audit_sensitivity(body="Just regular prose, no secrets.")
    assert issues == []


def test_audit_sensitivity_high_conf_secret_in_public_is_error() -> None:
    issues = v.audit_sensitivity(
        body="key=AKIAIOSFODNN7EXAMPLE",
        declared_sensitivity="public",
    )
    assert issues
    assert any(i.code == "SD-001" and i.severity == "error" for i in issues)


def test_audit_sensitivity_high_conf_secret_in_confidential_is_warning() -> None:
    issues = v.audit_sensitivity(
        body="key=AKIAIOSFODNN7EXAMPLE",
        declared_sensitivity="confidential",
    )
    assert issues
    assert all(i.severity == "warning" for i in issues if i.code == "SD-001")


def test_audit_sensitivity_pii_only_is_warning() -> None:
    """PII (lower confidence) is a warning regardless of declared level."""
    issues = v.audit_sensitivity(
        body="Contact john@example.com on +1 555 123 4567 about onboarding.",
        declared_sensitivity="public",
    )
    assert any(i.code == "SD-001" and i.severity == "warning" for i in issues)


# ============================================================================ #
# FM-007: sensitivity field expected on certain document types                  #
# ============================================================================ #


def test_audit_frontmatter_fm007_fires_for_module_spec_without_sensitivity() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        owner="x",
        source_of_truth=True,
        frontmatter={},
    )
    assert any(i.code == "FM-007" for i in issues)


def test_audit_frontmatter_fm007_quiet_when_sensitivity_present() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.MODULE_SPEC,
        status=DocumentStatus.DRAFT,
        owner="x",
        source_of_truth=True,
        frontmatter={"sensitivity": "internal"},
    )
    assert not any(i.code == "FM-007" for i in issues)


def test_audit_frontmatter_fm007_quiet_for_guide_type() -> None:
    """FM-007 only applies to spec/architecture/standard types."""
    issues = v.audit_frontmatter(
        type=DocumentType.GUIDE,
        status=DocumentStatus.DRAFT,
        owner="x",
        source_of_truth=True,
        frontmatter={},
    )
    assert not any(i.code == "FM-007" for i in issues)


def test_audit_frontmatter_fm007_fires_for_architecture() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.ARCHITECTURE,
        status=DocumentStatus.ACTIVE,
        owner="x",
        source_of_truth=True,
        frontmatter={},
    )
    assert any(i.code == "FM-007" for i in issues)


def test_audit_frontmatter_fm007_fires_for_standard() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.STANDARD,
        status=DocumentStatus.ACTIVE,
        owner="x",
        source_of_truth=True,
        frontmatter={},
    )
    assert any(i.code == "FM-007" for i in issues)


# ============================================================================ #
# SD-002 — projection redaction by audience                                     #
# ============================================================================ #


def _run_alembic_upgrade(db_url: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "COD_DOC_DB_URL": db_url}
    venv_alembic = REPO_ROOT / ".venv" / "bin" / "alembic"
    cmd = [str(venv_alembic) if venv_alembic.exists() else "alembic", "upgrade", "head"]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, env=env, capture_output=True)


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'sens.db'}"


@pytest.fixture
def engine_with_schema(db_url: str):  # type: ignore[no-untyped-def]
    _run_alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield engine
    engine.dispose()


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    p = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    p.created = now
    p.updated = now
    session.add(p)
    session.flush()
    return p.row_id


def _create_doc(
    session: Session, project_id: int, *, sensitivity: Sensitivity, body: str = "secret content"
) -> int:
    doc = docs.create(
        session,
        project_id=project_id,
        doc_key="restricted-spec",
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        title="Restricted Spec",
        sensitivity=sensitivity,
        author="human:test",
        owner="human:test",
    )
    docs.add_section(
        session,
        document_id=doc.row_id,
        anchor="i",
        heading="I",
        level=2,
        position=0,
        body=body,
        author="human:test",
    )
    return doc.row_id  # type: ignore[return-value]


def test_render_markdown_no_audience_returns_full_body(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        did = _create_doc(session, pid, sensitivity=Sensitivity.CONFIDENTIAL, body="secret-payload")
        rendered = proj.render_markdown(session, did)
        assert "secret-payload" in rendered


def test_render_markdown_public_audience_redacts_confidential(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        did = _create_doc(session, pid, sensitivity=Sensitivity.CONFIDENTIAL, body="secret-payload")
        rendered = proj.render_markdown(session, did, audience="public")
        assert "secret-payload" not in rendered
        assert "content redacted" in rendered
        assert "confidential" in rendered  # marker mentions the level


def test_render_markdown_internal_audience_sees_internal(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        did = _create_doc(session, pid, sensitivity=Sensitivity.INTERNAL, body="internal-doc")
        rendered = proj.render_markdown(session, did, audience="internal")
        assert "internal-doc" in rendered


def test_render_markdown_redaction_is_reproducible(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Calling render twice with the same audience yields identical output."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        did = _create_doc(session, pid, sensitivity=Sensitivity.RESTRICTED, body="payload")
        a = proj.render_markdown(session, did, audience="public")
        b = proj.render_markdown(session, did, audience="public")
        assert a == b


def test_export_with_public_audience_does_not_overwrite_projection_hash(  # type: ignore[no-untyped-def]
    engine_with_schema, tmp_path: Path
) -> None:
    """Audience-specific export must NOT clobber the canonical projection_hash."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        pid = _seed_project(session)
        did = _create_doc(session, pid, sensitivity=Sensitivity.CONFIDENTIAL, body="secret-stuff")
        # First, do canonical export to seed projection_hash.
        proj.export_document(session, did, root_path=tmp_path)
        from cod_doc.infra.models import DocumentModel

        canonical_hash = session.get(DocumentModel, did).projection_hash
        assert canonical_hash is not None

        # Audience export to a sibling directory must not change the hash.
        public_dir = tmp_path / "public"
        public_dir.mkdir()
        proj.export_document(session, did, root_path=public_dir, audience="public")
        assert session.get(DocumentModel, did).projection_hash == canonical_hash
        # And the public file must NOT contain the secret body.
        from cod_doc.infra.models import DocumentModel as _DM

        path = session.get(_DM, did).path
        public_file = (public_dir / path).read_text(encoding="utf-8")
        assert "secret-stuff" not in public_file
        assert "content redacted" in public_file
