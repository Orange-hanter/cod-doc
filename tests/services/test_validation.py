"""COD-020: validation module — structural validators + advisory audits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType, TaskType
from cod_doc.services import validation as v

# ============================================================================ #
# task_id                                                                       #
# ============================================================================ #


@pytest.mark.parametrize("good", ["AUTH-025", "COD-011", "ML-009A", "AGN-021A", "ABCDE-123"])
def test_validate_task_id_accepts_valid(good: str) -> None:
    v.validate_task_id(good)  # no raise


@pytest.mark.parametrize(
    "bad",
    [
        "p-001",         # lowercase
        "P-001",         # single-letter prefix
        "ABCDEF-001",    # 6-letter prefix (>5)
        "AB-1",          # 1-digit number
        "AB-1234",       # 4-digit number
        "AB-001a",       # lowercase suffix
        "AB001",         # missing dash
        "",              # empty
    ],
)
def test_validate_task_id_rejects_invalid(bad: str) -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_task_id(bad)
    assert exc.value.code == "TP-001"
    assert exc.value.details["task_id"] == bad


# ============================================================================ #
# id_prefix                                                                     #
# ============================================================================ #


@pytest.mark.parametrize("good", ["AB", "ABC", "ABCD", "ABCDE", "AUTH"])
def test_validate_id_prefix_accepts(good: str) -> None:
    v.validate_id_prefix(good)


@pytest.mark.parametrize("bad", ["A", "ABCDEF", "Ab", "a1", "", "1A"])
def test_validate_id_prefix_rejects(bad: str) -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_id_prefix(bad)
    assert exc.value.code == "TP-002"


# ============================================================================ #
# story_id                                                                      #
# ============================================================================ #


@pytest.mark.parametrize("good", ["US-001", "US-014", "EPIC-099"])
def test_validate_story_id_accepts(good: str) -> None:
    v.validate_story_id(good)


@pytest.mark.parametrize("bad", ["us-001", "U-001", "UUUUU-001", "US-1", "US-0001", ""])
def test_validate_story_id_rejects(bad: str) -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_story_id(bad)
    assert exc.value.code == "US-001"


# ============================================================================ #
# section slug                                                                  #
# ============================================================================ #


@pytest.mark.parametrize("good", ["A-Data-Core", "B-Services", "C-Write-Paths", "Z-X9"])
def test_validate_section_slug_accepts(good: str) -> None:
    v.validate_section_slug(good)


@pytest.mark.parametrize("bad", ["a-data", "AA-Data", "1-Data", "A-", "A_data", ""])
def test_validate_section_slug_rejects(bad: str) -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_section_slug(bad)
    assert exc.value.code == "TP-003"


# ============================================================================ #
# forbidden task types                                                          #
# ============================================================================ #


def test_validate_task_type_rejects_forbidden_alias() -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_task_type("implementation")
    assert exc.value.code == "TP-005"
    assert "feature" in str(exc.value)


def test_validate_task_type_rejects_compound() -> None:
    with pytest.raises(v.ValidationError) as exc:
        v.validate_task_type("migration+feature")
    assert exc.value.code == "TP-005"


def test_validate_task_type_passes_known_values() -> None:
    # Known-good values from TaskType enum are accepted (no raise).
    for ok in ("feature", "test", "bug", "refactor", "migration", "docs", "chore"):
        v.validate_task_type(ok)


# ============================================================================ #
# audit_task_title (advisory)                                                   #
# ============================================================================ #


def test_audit_task_title_recognized_pattern_matches_type() -> None:
    issues = v.audit_task_title("Test + Implement: TaskService", TaskType.FEATURE)
    assert issues == []


def test_audit_task_title_recognized_pattern_mismatches_type() -> None:
    issues = v.audit_task_title("Test: parser", TaskType.FEATURE)
    assert len(issues) == 1
    assert issues[0].code == "TP-004"
    assert issues[0].severity == "error"
    assert issues[0].details["expected"] == "test"


def test_audit_task_title_unknown_pattern_warns() -> None:
    issues = v.audit_task_title("Just do the thing", TaskType.FEATURE)
    assert len(issues) == 1
    assert issues[0].severity == "warning"


def test_audit_task_title_empty_is_error() -> None:
    issues = v.audit_task_title("   ", TaskType.FEATURE)
    assert any(i.severity == "error" for i in issues)


def test_audit_task_title_implement_maps_to_feature() -> None:
    assert v.audit_task_title("Implement: foo bar", TaskType.FEATURE) == []
    issues = v.audit_task_title("Implement: foo bar", TaskType.TEST)
    assert issues and issues[0].code == "TP-004"


def test_audit_task_title_fix_maps_to_bug() -> None:
    assert v.audit_task_title("Fix: race in worker", TaskType.BUG) == []


# ============================================================================ #
# audit_frontmatter (advisory)                                                  #
# ============================================================================ #


def test_audit_frontmatter_active_requires_owner() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        owner=None,
        source_of_truth=True,
    )
    assert any(i.code == "FM-002" for i in issues)


def test_audit_frontmatter_active_with_owner_ok() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        owner="backend-team",
        source_of_truth=True,
    )
    assert not any(i.code == "FM-002" for i in issues)


def test_audit_frontmatter_non_canonical_requires_canonical_source() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.REDIRECT,
        status=DocumentStatus.DEPRECATED,
        owner="x",
        source_of_truth=False,
    )
    assert any(i.code == "FM-003" for i in issues)


def test_audit_frontmatter_non_canonical_with_canonical_source_ok() -> None:
    issues = v.audit_frontmatter(
        type=DocumentType.REDIRECT,
        status=DocumentStatus.DEPRECATED,
        owner="x",
        source_of_truth=False,
        frontmatter={"canonical_source": "modules/M1-auth/overview"},
    )
    assert not any(i.code == "FM-003" for i in issues)


def test_audit_frontmatter_last_updated_in_future_warns() -> None:
    now = datetime(2026, 4, 28, tzinfo=UTC)
    issues = v.audit_frontmatter(
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        owner="x",
        source_of_truth=True,
        last_updated=now + timedelta(days=2),
        now=now,
    )
    assert any(i.code == "FM-004" for i in issues)


def test_audit_frontmatter_stale_active_warns() -> None:
    now = datetime(2026, 4, 28, tzinfo=UTC)
    issues = v.audit_frontmatter(
        type=DocumentType.GUIDE,
        status=DocumentStatus.ACTIVE,
        owner="x",
        source_of_truth=True,
        last_updated=now - timedelta(days=200),
        now=now,
    )
    assert any(i.code == "FM-005" for i in issues)


def test_audit_frontmatter_stale_does_not_apply_to_draft() -> None:
    now = datetime(2026, 4, 28, tzinfo=UTC)
    issues = v.audit_frontmatter(
        type=DocumentType.GUIDE,
        status=DocumentStatus.DRAFT,
        owner="x",
        source_of_truth=True,
        last_updated=now - timedelta(days=400),
        now=now,
    )
    assert not any(i.code == "FM-005" for i in issues)


# ============================================================================ #
# ValidationError shape                                                         #
# ============================================================================ #


def test_validation_error_carries_code_and_details() -> None:
    try:
        v.validate_task_id("bad")
    except v.ValidationError as e:
        assert e.code == "TP-001"
        assert "task_id" in e.details
        assert isinstance(e, ValueError)  # subclass of ValueError
    else:
        pytest.fail("expected ValidationError")
